"""Bit-wise Splight differential model for instantiating E0/E1 trails."""

from argparse import ArgumentParser, RawTextHelpFormatter
import contextlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time

import yaml

try:
    from gurobipy import GRB, quicksum, read
except Exception:  # pragma: no cover
    GRB = None
    read = None

from splight.sbox_constraints import SBOX_INEQUALITIES
from splight.splight_spec import nibble_to_bits


TMP_DIR = os.path.join("tmp", "boomerang")
PROJECT_DIR = Path(__file__).resolve().parent


class _TeeTextStream(io.TextIOBase):
    """Mirror command-line output to the terminal and a result log."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


@contextlib.contextmanager
def _tee_terminal_output(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        with contextlib.redirect_stdout(_TeeTextStream(sys.stdout, handle)):
            with contextlib.redirect_stderr(_TeeTextStream(sys.stderr, handle)):
                yield


class Diff:
    """
    This class is used to find differential trails and to compute the
    differential effect for Splight in the boomerang search.

    Bit variable naming:
        x_roundNumber_nibbleNumber_bitNumber
        x_roundNumber_nibbleNumber_0: msb
        x_roundNumber_nibbleNumber_3: lsb

    Round variable mapping, with 16-nibble state x_r = L_r || R_r:

        x_r_0..x_r_7  -- S -->  y_r_0..y_r_7
        y_r_0..y_r_7  -- L -->  l_r_0..l_r_7
        l_r_0..l_r_7  -- S -->  z_r_0..z_r_7
        z_r_i xor x_r_{8+i} maps directly to x_{r+1}_{(i-2) mod 8}
        x_r_i maps directly to x_{r+1}_{8+i}

    There is no separate t variable in this model because the post-xor SHI2
    layer is only a nibble rotation and is encoded directly in the x_{r+1}
    index.
    """

    def __init__(self, params, direction="forward", fixed_variables=None, time_limit=None):
        if isinstance(params, dict):
            self.nrounds = int(params.get("nrounds", 1))
            self.direction = params.get("direction", direction)
            self.fixed_variables = params.get("fixedVariables", {})
            self.nonzero_variables = params.get("nonzeroVariables", [])
            self.truncated_masks = params.get("truncatedMasks", {})
            self.time_limit = params.get("timelimit", time_limit)
            self.mode = params.get("mode", 0)
            self.start_weight = params.get("startweight", 0)
            self.end_weight = params.get("endweight", 128)
            self.number_of_trails = params.get("numberoftrails", 1)
            self.round_offset = int(params.get("round_offset", 0))
        else:
            self.nrounds = int(params)
            self.direction = direction
            self.fixed_variables = fixed_variables or {}
            self.nonzero_variables = []
            self.truncated_masks = {}
            self.time_limit = time_limit
            self.mode = 0
            self.start_weight = 0
            self.end_weight = 128
            self.number_of_trails = 1
            self.round_offset = 0
        self.branch_size = 8
        self.state_size = 16
        self.shift_nibbles = 2
        self.milp_variables = []
        self.xor_counter = 0
        os.makedirs(TMP_DIR, exist_ok=True)
        self.lp_file_name = os.path.join(
            TMP_DIR, f"splight_h_diff_{os.getpid()}_{int(time.time() * 1000)}.lp"
        )
        self.milp_model = None
        self.weight_terms = []
        self.sbox_pr_groups = []
        self.sbox_inequalities = SBOX_INEQUALITIES
        self.total_weight = 0
        self.last_solve_status = None
        self.exact_nogood_count = 0
        self._start_weight_constraint_added = False

    @staticmethod
    def ordered_set(seq):
        seen = set()
        out = []
        for item in seq:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def nibble_bits(self, base):
        names = [f"{base}_{bit}" for bit in range(4)]
        self.milp_variables.extend(names)
        return names

    def generate_bit_variables(self):
        xs = []
        for r in range(self.nrounds + 1):
            state = []
            for n in range(self.state_size):
                state.append(self.nibble_bits(f"x_{r}_{n}"))
            xs.append(state)
        return xs

    def state_variables(self, r):
        return [self.nibble_bits(f"x_{r}_{n}") for n in range(self.state_size)]

    def generate_round_x_variables(self, rn):
        """LBlock-compatible alias for the full round state variables."""
        return self.state_variables(rn)

    def round_variables(self, r):
        ys = [self.nibble_bits(f"y_{r}_{i}") for i in range(self.branch_size)]
        ls = [self.nibble_bits(f"l_{r}_{i}") for i in range(self.branch_size)]
        zs = [self.nibble_bits(f"z_{r}_{i}") for i in range(self.branch_size)]
        return ys, ls, zs

    def generate_round_y_variables(self, rn):
        ys, _, _ = self.round_variables(rn)
        return ys

    def generate_round_l_variables(self, rn):
        _, ls, _ = self.round_variables(rn)
        return ls

    def generate_round_z_variables(self, rn):
        _, _, zs = self.round_variables(rn)
        return zs

    def sbox_pr_variables(self, r, layer, i):
        names = [f"pr_{r}_{layer}_{i}_{bit}" for bit in range(3)]
        self.milp_variables.extend(names)
        return names

    def generate_round_pr_variables(self, rn):
        pr = []
        for layer in ("y", "z"):
            for i in range(self.branch_size):
                pr.append(self.sbox_pr_variables(rn, layer, i))
        return pr

    def eq_bits(self, left, right):
        return [f"{u} - {v} = 0" for u, v in zip(left, right)]

    def constraints_by_equality(self, a, b):
        return f"{a} - {b} = 0"

    def constraint_by_nibble_equality(self, a, b):
        return self.eq_bits(a, b)

    def xor_bit(self, u, v, w):
        return [
            f"{u} + {v} + {w} <= 2",
            f"{u} + {v} - {w} >= 0",
            f"{u} - {v} + {w} >= 0",
            f"- {u} + {v} + {w} >= 0",
        ]

    def constraints_by_xor(self, a, b, c):
        return self.xor_bit(a, b, c)

    def xor_nibble(self, left, right, out):
        constraints = []
        for u, v, w in zip(left, right, out):
            constraints.extend(self.xor_bit(u, v, w))
        return constraints

    def constraints_by_nibble_xor(self, a, b, c):
        return self.xor_nibble(a, b, c)

    def xor_many_nibbles(self, inputs, output):
        if len(inputs) == 1:
            return self.eq_bits(inputs[0], output)
        constraints = []
        acc = inputs[0]
        for idx, item in enumerate(inputs[1:], start=1):
            out = output if idx == len(inputs) - 1 else self.nibble_bits(f"d_{self.xor_counter}")
            if out is not output:
                self.xor_counter += 1
            constraints.extend(self.xor_nibble(acc, item, out))
            acc = out
        return constraints

    def constraints_by_sbox(self, di, do, pr):
        """
        Generate fixed Splight S-box DDT constraints by replacing variables in
        self.sbox_inequalities.

        di: 4 input-difference bit variables, a0 is msb.
        do: 4 output-difference bit variables, b0 is msb.
        pr: 3 probability-weight bit variables.
        """
        if len(di) != 4 or len(do) != 4 or len(pr) != 3:
            raise ValueError("S-box constraints require 4 input bits, 4 output bits, and 3 pr bits")
        constraints = []
        for inequality in self.sbox_inequalities:
            replaced = inequality
            for bit in range(4):
                replaced = replaced.replace(f"a{bit}", di[bit])
                replaced = replaced.replace(f"b{bit}", do[bit])
            for bit in range(3):
                replaced = replaced.replace(f"pr{bit}", pr[bit])
            constraints.append(replaced.replace("*", " "))
        return constraints

    def generate_sbox_ddt_constraints(self, sbox_in, sbox_out, tag):
        pr = self.sbox_pr_variables(tag[0], tag[1], tag[2])
        self.sbox_pr_groups.append({"tag": tag, "pr": pr})
        self.weight_terms.extend((1, bit) for bit in pr)
        return self.constraints_by_sbox(sbox_in, sbox_out, pr)

    def generate_linear_layer_bit_constraints(self, r):
        ys, ls, _ = self.round_variables(r)
        constraints = []
        for base in (0, 4):
            constraints.extend(self.xor_many_nibbles([ls[base + 3], ys[base + 3]], ls[base]))
            constraints.extend(self.eq_bits(ys[base], ls[base + 1]))
            constraints.extend(self.xor_many_nibbles([ys[base + 1], ys[base + 2]], ls[base + 2]))
            constraints.extend(self.xor_many_nibbles([ys[base], ys[base + 2]], ls[base + 3]))
        return constraints

    def generate_round_constraints_forward(self, r):
        x_in = self.state_variables(r)
        x_out = self.state_variables(r + 1)
        ys, ls, zs = self.round_variables(r)
        constraints = []
        for i in range(self.branch_size):
            constraints.extend(self.generate_sbox_ddt_constraints(x_in[i], ys[i], tag=(r, "y", i)))
        constraints.extend(self.generate_linear_layer_bit_constraints(r))
        for i in range(self.branch_size):
            constraints.extend(self.generate_sbox_ddt_constraints(ls[i], zs[i], tag=(r, "z", i)))
            out_idx = (i - self.shift_nibbles) % self.branch_size
            constraints.extend(self.xor_nibble(zs[i], x_in[self.branch_size + i], x_out[out_idx]))
            constraints.extend(self.eq_bits(x_in[i], x_out[self.branch_size + i]))
        return constraints

    def generate_round_constraints_backward(self, r):
        x_in = self.state_variables(r)
        x_out = self.state_variables(r + 1)
        ys, ls, zs = self.round_variables(r)
        constraints = []
        for i in range(self.branch_size):
            constraints.extend(self.generate_sbox_ddt_constraints(x_in[i], ys[i], tag=(r, "y", i)))
        constraints.extend(self.generate_linear_layer_bit_constraints(r))
        for i in range(self.branch_size):
            constraints.extend(self.generate_sbox_ddt_constraints(ls[i], zs[i], tag=(r, "z", i)))
            out_idx = (i - self.shift_nibbles) % self.branch_size
            constraints.extend(self.xor_nibble(zs[i], x_out[out_idx], x_in[self.branch_size + i]))
            constraints.extend(self.eq_bits(x_in[i], x_out[self.branch_size + i]))
        return constraints

    def declare_fixed_variables(self):
        constraints = []
        for name, value in self.fixed_variables.items():
            parts = name.split("_")
            if len(parts) == 2 and parts[0] == "x":
                bits = f"{int(value, 16):064b}" if isinstance(value, str) else f"{int(value):064b}"
                state = [bit for n in self.state_variables(int(parts[1])) for bit in n]
                constraints.extend(f"{var} = {val}" for var, val in zip(state, bits))
            elif len(parts) == 3 and parts[0] == "x":
                bits = nibble_to_bits(int(value, 16) if isinstance(value, str) else int(value))
                constraints.extend(f"x_{parts[1]}_{parts[2]}_{i} = {bit}" for i, bit in enumerate(bits))
            elif len(parts) == 4:
                constraints.append(f"{name} = {int(value)}")
        return constraints

    def declare_nonzero_variables(self):
        """Require each truncated-active concrete nibble to be nonzero.

        A truncated mask value of one means an active nibble, not merely an
        unconstrained nibble.  Keeping this separate from fixedVariables lets
        boom.py use the same Diff model for ordinary and exact refinement.
        """
        constraints = []
        for name in self.nonzero_variables:
            parts = name.split("_")
            if len(parts) != 3 or parts[0] not in ("x", "y", "l", "z"):
                raise ValueError(f"invalid nonzero nibble variable: {name}")
            constraints.append(" + ".join(self.nibble_bits(name)) + " >= 1")
        return constraints

    def declare_truncated_masks(self):
        """Fix inactive nibbles to zero and require active nibbles nonzero.

        A mask is a compact representation of a truncated trail support.  It
        deliberately does not fix an active nibble to a concrete hexadecimal
        value, so the MILP remains free to instantiate a characteristic.
        """
        constraints = []
        for name, mask in self.truncated_masks.items():
            parts = name.split("_")
            if len(parts) != 2 or parts[0] not in ("x", "y", "l", "z"):
                raise ValueError(f"invalid truncated mask variable: {name}")
            round_index = int(parts[1])
            if parts[0] == "x":
                if not 0 <= round_index <= self.nrounds or len(mask) != 16:
                    raise ValueError(f"invalid state truncated mask: {name}={mask!r}")
                variables = self.state_variables(round_index)
            else:
                if not 0 <= round_index < self.nrounds or len(mask) != 8:
                    raise ValueError(f"invalid round truncated mask: {name}={mask!r}")
                ys, ls, zs = self.round_variables(round_index)
                variables = {"y": ys, "l": ls, "z": zs}[parts[0]]
            if any(value not in "01" for value in mask):
                raise ValueError(f"truncated mask must contain only 0/1: {name}={mask!r}")
            for nibble, active in zip(variables, mask):
                if active == "0":
                    constraints.extend(f"{bit} = 0" for bit in nibble)
                else:
                    constraints.append(" + ".join(nibble) + " >= 1")
        return constraints

    def flatten_state(self, s):
        return [bit for nibble in s for bit in nibble]

    def nibble_value(self, bits):
        return int("".join(str(int(round(self.milp_model.getVarByName(bit).X))) for bit in bits), 2)

    def nibbles_to_hex_from_bits(self, nibbles):
        return "".join(f"{self.nibble_value(bits):X}" for bits in nibbles)

    def generate_objective_function(self):
        if not self.weight_terms:
            return "0"
        return " + ".join(f"{weight} {var}" for weight, var in self.weight_terms)

    def make_model(self):
        self.milp_variables = []
        self.xor_counter = 0
        self.weight_terms = []
        self.sbox_pr_groups = []
        constraints = self.generate_constraints()
        constraints.extend(self.exclude_trivial_trail())
        constraints.extend(self.declare_fixed_variables())
        constraints.extend(self.declare_truncated_masks())
        constraints.extend(self.declare_nonzero_variables())
        lp = "\\ Splight bit-wise differential model\n"
        lp += "Minimize\n" + self.generate_objective_function() + "\n"
        lp += "Subject To\n" + "\n".join(constraints) + "\n"
        self.milp_variables = self.ordered_set(self.milp_variables)
        lp += "Binary\n" + "\n".join(self.milp_variables) + "\nEnd\n"
        with open(self.lp_file_name, "w", encoding="utf-8") as handle:
            handle.write(lp)
        return self.lp_file_name

    def generate_constraints(self):
        """Generate round propagation constraints, mirroring lblock.diff."""
        constraints = []
        for r in range(self.nrounds):
            if self.direction == "backward":
                constraints.extend(self.generate_round_constraints_backward(r))
            else:
                constraints.extend(self.generate_round_constraints_forward(r))
        return constraints

    def exclude_trivial_trail(self):
        input_bits = [bit for nibble in self.state_variables(0) for bit in nibble]
        return [" + ".join(input_bits) + " >= 1"]

    def parse_solver_output(self):
        get_bit_value = lambda name: str(int(round(self.milp_model.getVarByName(name).X)))
        characteristic = {}
        for r in range(self.nrounds + 1):
            state = self.state_variables(r)
            x = self.flatten_state(state)
            bits = "".join(map(get_bit_value, x))
            characteristic[f"x_{r}"] = hex(int(bits, 2))[2:].zfill(16).upper()
            if r < self.nrounds:
                ys, ls, zs = self.round_variables(r)
                characteristic[f"y_{r}"] = self.nibbles_to_hex_from_bits(ys)
                characteristic[f"l_{r}"] = self.nibbles_to_hex_from_bits(ls)
                # Under a single key the round-key difference is always zero,
                # hence the post-ARK difference is exactly the linear output.
                characteristic[f"rk_{r}"] = "00000000"
                characteristic[f"ak_{r}"] = characteristic[f"l_{r}"]
                characteristic[f"z_{r}"] = self.nibbles_to_hex_from_bits(zs)
                round_weight = 0
                for group in self.sbox_pr_groups:
                    tag = group["tag"]
                    if tag is None or tag[0] != r:
                        continue
                    round_weight += sum(
                        int(round(self.milp_model.getVarByName(bit).X))
                        for bit in group["pr"]
                    )
                characteristic[f"pr_{r}"] = f"-{round_weight}"
            else:
                characteristic[f"y_{r}"] = "none"
                characteristic[f"l_{r}"] = "none"
                characteristic[f"rk_{r}"] = "none"
                characteristic[f"ak_{r}"] = "none"
                characteristic[f"z_{r}"] = "none"
                characteristic[f"pr_{r}"] = "none"
        characteristic["total_weight"] = "%0.02f" % self.total_weight
        characteristic["nrounds"] = self.nrounds
        characteristic["round_offset"] = self.round_offset
        return characteristic

    def build_exact_verify_trail(self, characteristic):
        """Convert one complete single-key MILP trail into verifier input."""
        fixed_diffs = {}
        for r in range(self.nrounds + 1):
            fixed_diffs[f"dX{r}"] = characteristic[f"x_{r}"]
            if r < self.nrounds:
                fixed_diffs[f"dY{r}"] = characteristic[f"y_{r}"]
                fixed_diffs[f"dLIN{r}"] = characteristic[f"l_{r}"]
                fixed_diffs[f"dAK{r}"] = characteristic[f"ak_{r}"]
                fixed_diffs[f"dZ{r}"] = characteristic[f"z_{r}"]
        return {
            "rounds": self.nrounds,
            "round_offset": self.round_offset,
            "fixed_diffs": fixed_diffs,
        }

    @staticmethod
    def _is_concrete_difference_bit_name(name):
        parts = name.split("_")
        return (
            len(parts) == 4
            and parts[0] in ("x", "y", "l", "z")
            and all(part.isdigit() for part in parts[1:])
        )

    def _binary_value(self, var):
        value = int(round(var.X))
        if value not in (0, 1) or abs(var.X - value) > 1e-5:
            raise RuntimeError(f"non-binary Gurobi value {var.X!r} for {var.VarName}")
        return value

    def concrete_solution_signature(self):
        """Return the complete concrete state-difference assignment.

        Probability selectors and temporary XOR variables are intentionally
        omitted: a no-good must reject only one characteristic, not one solver
        encoding of the same characteristic.
        """
        if self.milp_model is None or not self.milp_model.SolCount:
            raise RuntimeError("no current Diff solution is available")
        assignment = []
        for var in sorted(self.milp_model.getVars(), key=lambda item: item.VarName):
            if self._is_concrete_difference_bit_name(var.VarName):
                assignment.append((var.VarName, self._binary_value(var)))
        if not assignment:
            raise RuntimeError("the concrete Diff signature is empty")
        return assignment

    def extract_solution_for_exact_verify(self):
        if self.milp_model is None or not self.milp_model.SolCount:
            raise RuntimeError("no current Diff solution is available")
        characteristic = self.parse_solver_output()
        return {
            "trail": self.build_exact_verify_trail(characteristic),
            "nogood_assignment": self.concrete_solution_signature(),
            "characteristic": characteristic,
            "weight": float(self.milp_model.ObjVal),
            "milp_status": self._gurobi_status_name(self.milp_model.Status),
            "model_id": id(self.milp_model),
        }

    def add_solution_nogood(self, solution, constraint_name=None):
        """Exclude exactly one complete concrete differential assignment."""
        if self.milp_model is None:
            raise RuntimeError("the Diff Gurobi model has not been loaded")
        assignment = solution.get("nogood_assignment", solution)
        if not assignment:
            raise ValueError("cannot add an empty concrete-trail no-good")
        terms = []
        for var_name, value in assignment:
            var = self.milp_model.getVarByName(var_name)
            if var is None:
                raise ValueError(f"unknown Diff variable in no-good: {var_name}")
            if value == 0:
                terms.append(var)
            elif value == 1:
                terms.append(1 - var)
            else:
                raise ValueError(f"invalid binary no-good value {value!r} for {var_name}")
        self.exact_nogood_count += 1
        name = constraint_name or f"exact_nogood_{self.exact_nogood_count:04d}"
        self.milp_model.addConstr(quicksum(terms) >= 1, name=name)
        self.milp_model.update()
        return name

    @staticmethod
    def print_trail(diff_trail):
        print("Rounds  x                 y         l         RK        ak        z         pr     ")
        print("-" * 82)
        for r in range(diff_trail["nrounds"] + 1):
            print(
                f"{r:<7} "
                f"{diff_trail.get(f'x_{r}', 'none'):<17} "
                f"{diff_trail.get(f'y_{r}', 'none'):<9} "
                f"{diff_trail.get(f'l_{r}', 'none'):<9} "
                f"{diff_trail.get(f'rk_{r}', 'none'):<9} "
                f"{diff_trail.get(f'ak_{r}', 'none'):<9} "
                f"{diff_trail.get(f'z_{r}', 'none'):<9} "
                f"{diff_trail.get(f'pr_{r}', 'none'):<7}"
            )
        print(f"Weight: -{diff_trail.get('total_weight', '0')}")

    @staticmethod
    def _gurobi_status_name(status):
        names = {
            GRB.OPTIMAL: "OPTIMAL",
            GRB.INFEASIBLE: "INFEASIBLE",
            GRB.INF_OR_UNBD: "INF_OR_UNBD",
            GRB.UNBOUNDED: "UNBOUNDED",
            GRB.TIME_LIMIT: "TIME_LIMIT",
            GRB.INTERRUPTED: "INTERRUPTED",
        }
        return names.get(status, f"GUROBI_STATUS_{status}")

    def load_model(self):
        if read is None:
            raise RuntimeError("gurobipy is required to solve the MILP")
        if not os.path.exists(self.lp_file_name):
            self.make_model()
        self.milp_model = read(self.lp_file_name)
        self.exact_nogood_count = 0
        self._start_weight_constraint_added = False
        return self.milp_model

    def optimize_next_candidate(self, output_flag=False, time_limit_override=None):
        """Optimize the loaded model without discarding previous no-goods."""
        if self.milp_model is None:
            self.load_model()
        self.milp_model.Params.OutputFlag = bool(output_flag)
        effective_time_limit = (
            time_limit_override if time_limit_override is not None else self.time_limit
        )
        if effective_time_limit is not None:
            self.milp_model.Params.TimeLimit = max(float(effective_time_limit), 1e-3)
        if self.start_weight is not None and not self._start_weight_constraint_added:
            self.milp_model.addConstr(
                self.milp_model.getObjective() >= self.start_weight,
                "start_weight_constraint",
            )
            self._start_weight_constraint_added = True
        started = time.time()
        self.milp_model.optimize()
        elapsed = time.time() - started
        self.last_solve_status = self._gurobi_status_name(self.milp_model.Status)
        if not self.milp_model.SolCount:
            return {
                "status": self.last_solve_status,
                "solution": None,
                "objective": None,
                "elapsed_seconds": elapsed,
            }
        self.total_weight = float(self.milp_model.ObjVal)
        return {
            "status": self.last_solve_status,
            "solution": self.parse_solver_output(),
            "objective": self.total_weight,
            "elapsed_seconds": elapsed,
        }

    def find_characteristic(self, deadline=None, time_limit_override=None):
        if deadline is not None:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            if time_limit_override is None:
                time_limit_override = remaining
            else:
                time_limit_override = min(float(time_limit_override), remaining)
        result = self.optimize_next_candidate(
            output_flag=False,
            time_limit_override=time_limit_override,
        )
        diff_trail = None
        if result["solution"] is not None:
            print(f"\nThe probability of the best differential characteristic: 2^-({self.total_weight})")
            print("\nDifferential trail:\n")
            diff_trail = result["solution"]
            self.print_trail(diff_trail)
        elif self.milp_model.Status == GRB.INFEASIBLE:
            print("The model is infeasible!")
        else:
            print(f"Differential search stopped: {result['status']}")
        print("Time used: %0.02f" % result["elapsed_seconds"])
        return diff_trail

    def solve(self, deadline=None, time_limit_override=None):
        if read is None:
            raise RuntimeError("gurobipy is required to solve the MILP")
        self.make_model()
        self.load_model()
        if self.mode == 0:
            return self.find_characteristic(
                deadline=deadline,
                time_limit_override=time_limit_override,
            )
        if self.mode == 2:
            return self.compute_differential_effect()
        print("Enter a number in [0, 1, 2], for the mode parameter please!")
        return None

    def compute_differential_effect(self, start_weight=None, end_weight=None):
        self.milp_model.Params.OutputFlag = False
        if self.time_limit is not None:
            self.milp_model.Params.TimeLimit = self.time_limit
        obj = self.milp_model.getObjective()
        if start_weight is None:
            start_weight = self.start_weight
        if end_weight is None:
            end_weight = self.end_weight
        if start_weight is not None:
            self.milp_model.addConstr(obj >= start_weight, "start_weight_constraint")
        self.milp_model.optimize()
        if self.milp_model.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.INTERRUPTED) and self.milp_model.SolCount:
            weight = self.milp_model.objVal
            if end_weight is not None and weight > end_weight:
                return 0
            print(f"Current weight: {weight}")
            print("Number of trails: 1")
            print(f"\tCurrent Probability: 2^({-weight})")
            return -weight
        if self.milp_model.Status == GRB.INFEASIBLE:
            print("The model is infeasible!")
        else:
            print("Unknown Error!")
        return 0


def loadparameters(args):
    """Get parameters from command line and optional yaml input file."""
    params = {
        "nrounds": 1,
        "mode": 0,
        "direction": "forward",
        "startweight": 0,
        "endweight": 128,
        "timelimit": 3600,
        "numberoftrails": 1,
        "fixedVariables": {},
    }
    if args.inputfile:
        with open(args.inputfile[0], "r", encoding="utf-8") as input_file:
            doc = yaml.load(input_file, Loader=yaml.FullLoader)
            params.update(doc)
            if "fixedVariables" in doc and isinstance(doc["fixedVariables"], list):
                fixed_vars = {}
                for variable in doc["fixedVariables"]:
                    fixed_vars = dict(list(fixed_vars.items()) + list(variable.items()))
                params["fixedVariables"] = fixed_vars
    if args.nrounds:
        params["nrounds"] = args.nrounds[0]
    if args.mode:
        params["mode"] = args.mode[0]
    if args.direction:
        params["direction"] = args.direction[0]
    if args.startweight:
        params["startweight"] = args.startweight[0]
    if args.endweight:
        params["endweight"] = args.endweight[0]
    if args.timelimit:
        params["timelimit"] = args.timelimit[0]
    if args.numberoftrails:
        params["numberoftrails"] = args.numberoftrails[0]
    return params


def _trail_text(trail):
    if not isinstance(trail, dict):
        return f"Differential effect log2: {trail}\n"
    lines = [
        "Rounds  x                 y         l         RK        ak        z         pr",
        "-" * 82,
    ]
    for r in range(int(trail["nrounds"]) + 1):
        lines.append(
            f"{r:<7} "
            f"{trail.get(f'x_{r}', 'none'):<17} "
            f"{trail.get(f'y_{r}', 'none'):<9} "
            f"{trail.get(f'l_{r}', 'none'):<9} "
            f"{trail.get(f'rk_{r}', 'none'):<9} "
            f"{trail.get(f'ak_{r}', 'none'):<9} "
            f"{trail.get(f'z_{r}', 'none'):<9} "
            f"{trail.get(f'pr_{r}', 'none'):<7}"
        )
    lines.append(f"Weight: -{trail.get('total_weight', '0')}")
    return "\n".join(lines) + "\n"


def _save_cli_result(output_dir, params, diff, result, elapsed_seconds):
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"round_{params['nrounds']}"
    payload = {
        "parameters": params,
        "status": diff.last_solve_status,
        "elapsed_seconds": elapsed_seconds,
        "lp_file": diff.lp_file_name,
        "trail": result if isinstance(result, dict) else None,
        "differential_effect_log2": result if not isinstance(result, dict) else None,
    }
    (output_dir / f"{stem}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    summary = [
        f"Rounds: {params['nrounds']}",
        f"Mode: {params['mode']}",
        f"Direction: {params['direction']}",
        f"Status: {diff.last_solve_status}",
        f"LP file: {diff.lp_file_name}",
        f"Total CLI time: {elapsed_seconds:.6f} seconds",
        "",
        _trail_text(result).rstrip(),
        "",
    ]
    (output_dir / f"{stem}.txt").write_text("\n".join(summary), encoding="utf-8")
    print(f"Saved result JSON: {output_dir / f'{stem}.json'}")
    print(f"Saved result text: {output_dir / f'{stem}.txt'}")
    print(f"Saved terminal log: {output_dir / 'terminal_print.txt'}")


def main():
    parser = ArgumentParser(
        description="Find Splight differential trails using a Gurobi MILP model",
        formatter_class=RawTextHelpFormatter,
    )
    parser.add_argument("--startweight", nargs=1, type=int, help="Starting weight for the trail search.")
    parser.add_argument("--endweight", nargs=1, type=int, help="Stop search after reaching endweight.")
    parser.add_argument("--nrounds", nargs=1, type=int, help="The number of rounds for the cipher.")
    parser.add_argument("--mode", nargs=1, type=int, choices=[0, 1, 2],
                        help="0 = search characteristic\n1 = reserved\n2 = compute differential effect")
    parser.add_argument("--direction", nargs=1, choices=["forward", "backward"],
                        help="Forward or backward Splight round convention.")
    parser.add_argument("--timelimit", nargs=1, type=int, help="Set a timelimit in seconds.")
    parser.add_argument("--inputfile", nargs=1, help="Use a yaml input file.")
    parser.add_argument("--numberoftrails", nargs=1, type=int, help="Number of trails.")
    params = loadparameters(parser.parse_args())
    output_dir = PROJECT_DIR / "result_diff" / f"round_{params['nrounds']}"
    with _tee_terminal_output(output_dir / "terminal_print.txt"):
        started = time.perf_counter()
        diff = Diff(params)
        diff.make_model()
        result = diff.solve()
        elapsed_seconds = time.perf_counter() - started
        _save_cli_result(output_dir, params, diff, result, elapsed_seconds)


if __name__ == "__main__":
    main()
