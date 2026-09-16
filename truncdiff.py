"""Nibble-level truncated differential propagation for Splight."""

import os
import time

try:
    from gurobipy import GRB, read
except Exception:  # pragma: no cover
    GRB = None
    read = None


TMP_DIR = "tmp"


class TruncDiff:
    def __init__(self, nrounds=1, prefix="", mode="standard", time_limit=None, lp_file_name=None):
        self.nrounds = nrounds
        self.prefix = prefix
        self.mode = mode
        self.time_limit = time_limit
        self.branch_size = 8
        self.state_size = 16
        self.shift_nibbles = 2
        self.milp_variables = []
        self.xor_counter = 0
        os.makedirs(TMP_DIR, exist_ok=True)
        self.lp_file_name = lp_file_name or os.path.join(
            TMP_DIR, f"splight_h_td_{os.getpid()}_{int(time.time() * 1000)}.lp"
        )
        self.milp_model = None

    @staticmethod
    def ordered_set(seq):
        seen = set()
        out = []
        for item in seq:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def _name(self, base):
        return f"{self.prefix}{base}" if self.prefix else base

    def generate_state_variables(self, r):
        xs = [self._name(f"x_{r}_{i}") for i in range(self.state_size)]
        self.milp_variables.extend(xs)
        return xs

    def generate_round_x_variables(self, rn, ul=None):
        """LBlock-compatible alias for truncated round state variables."""
        return self.generate_state_variables(rn)

    def generate_round_variables(self, r):
        ys = [self._name(f"y_{r}_{i}") for i in range(self.branch_size)]
        ls = [self._name(f"l_{r}_{i}") for i in range(self.branch_size)]
        zs = [self._name(f"z_{r}_{i}") for i in range(self.branch_size)]
        self.milp_variables.extend(ys + ls + zs)
        return ys, ls, zs

    def constraints_by_equality(self, u, v):
        return [f"{u} - {v} = 0"]

    def constraint_by_trunc_xor(self, u, v, w):
        """
        Truncated XOR support:
            (u, v) |----> w = u + v

        At the truncated level this allows cancellation and only forbids
        exactly one active variable among u, v, w.
        """
        return [
            f"{u} + {v} - {w} >= 0",
            f"{u} - {v} + {w} >= 0",
            f"- {u} + {v} + {w} >= 0",
        ]

    def constraint_by_xor(self, u, v, w):
        """
        Probability-one XOR activity propagation:
            (u, v) |----> w = u + v

        This models w as the OR of the two input activity variables and is used
        in the middle boomerang switch part.
        """
        return [
            f"{w} - {u} >= 0",
            f"{w} - {v} >= 0",
            f"{u} + {v} - {w} >= 0",
        ]

    def constraints_by_xor_standard(self, u, v, w):
        return self.constraint_by_trunc_xor(u, v, w)

    def constraints_by_xor_prob_one(self, u, v, w):
        return self.constraint_by_xor(u, v, w)

    def constraints_by_many_trunc_xor(self, inputs, output):
        if len(inputs) == 1:
            return self.constraints_by_equality(inputs[0], output)
        constraints = []
        acc = inputs[0]
        for idx, item in enumerate(inputs[1:], start=1):
            out = output if idx == len(inputs) - 1 else self._name(f"d_{self.xor_counter}")
            if out != output:
                self.milp_variables.append(out)
                self.xor_counter += 1
            constraints.extend(self.constraint_by_trunc_xor(acc, item, out))
            acc = out
        return constraints

    def constraints_by_many_xor(self, inputs, output):
        if len(inputs) == 1:
            return self.constraints_by_equality(inputs[0], output)
        constraints = []
        acc = inputs[0]
        for idx, item in enumerate(inputs[1:], start=1):
            out = output if idx == len(inputs) - 1 else self._name(f"d_{self.xor_counter}")
            if out != output:
                self.milp_variables.append(out)
                self.xor_counter += 1
            constraints.extend(self.constraint_by_xor(acc, item, out))
            acc = out
        return constraints

    def _xor_many(self, inputs, output, mode):
        if mode in ("prob_one", "probability-one", "probability_one", "exact"):
            return self.constraints_by_many_xor(inputs, output)
        return self.constraints_by_many_trunc_xor(inputs, output)

    def select_xor_function(self, mode):
        if mode in ("prob_one", "probability-one", "probability_one", "exact"):
            return self.constraint_by_xor
        return self.constraint_by_trunc_xor

    def select_many_xor_function(self, mode):
        if mode in ("prob_one", "probability-one", "probability_one", "exact"):
            return self.constraints_by_many_xor
        return self.constraints_by_many_trunc_xor

    def constraints_by_linear_layer(self, r, mode):
        ys, ls, _ = self.generate_round_variables(r)
        many_xor = self.select_many_xor_function(mode)
        constraints = []
        for base in (0, 4):
            constraints.extend(many_xor([ls[base + 3], ys[base + 3]], ls[base]))
            constraints.extend(self.constraints_by_equality(ys[base], ls[base + 1]))
            constraints.extend(many_xor([ys[base + 1], ys[base + 2]], ls[base + 2]))
            constraints.extend(many_xor([ys[base], ys[base + 2]], ls[base + 3]))
        return constraints

    def generate_round_constraints_forward(self, r, mode=None):
        mode = mode or self.mode
        xor = self.select_xor_function(mode)
        x_in = self.generate_state_variables(r)
        x_out = self.generate_state_variables(r + 1)
        ys, ls, zs = self.generate_round_variables(r)
        constraints = []
        for i in range(self.branch_size):
            constraints.extend(self.constraints_by_equality(x_in[i], ys[i]))
        constraints.extend(self.constraints_by_linear_layer(r, mode))
        for i in range(self.branch_size):
            constraints.extend(self.constraints_by_equality(ls[i], zs[i]))
            out_idx = (i - self.shift_nibbles) % self.branch_size
            constraints.extend(xor(zs[i], x_in[self.branch_size + i], x_out[out_idx]))
            constraints.extend(self.constraints_by_equality(x_in[i], x_out[self.branch_size + i]))
        return constraints

    def generate_round_constraints_forward_trunc_xor(self, r):
        return self.generate_round_constraints_forward(r, "standard")

    def generate_round_constraints_forward_xor(self, r):
        return self.generate_round_constraints_forward(r, "prob_one")

    def generate_round_constraints_backward(self, r, mode=None):
        """Inverse-round support from x_r to x_{r+1} in lower-trail convention."""
        mode = mode or self.mode
        xor = self.select_xor_function(mode)
        x_in = self.generate_state_variables(r)
        x_out = self.generate_state_variables(r + 1)
        ys, ls, zs = self.generate_round_variables(r)
        constraints = []
        for i in range(self.branch_size):
            constraints.extend(self.constraints_by_equality(x_in[i], ys[i]))
        constraints.extend(self.constraints_by_linear_layer(r, mode))
        for i in range(self.branch_size):
            constraints.extend(self.constraints_by_equality(ls[i], zs[i]))
            out_idx = (i - self.shift_nibbles) % self.branch_size
            constraints.extend(xor(zs[i], x_out[out_idx], x_in[self.branch_size + i]))
            constraints.extend(self.constraints_by_equality(x_in[i], x_out[self.branch_size + i]))
        return constraints

    def generate_round_constraints_backward_trunc_xor(self, r):
        return self.generate_round_constraints_backward(r, "standard")

    def generate_round_constraints_backward_xor(self, r):
        return self.generate_round_constraints_backward(r, "prob_one")

    def active_sbox_vars(self, r):
        x = self.generate_state_variables(r)
        _, ls, _ = self.generate_round_variables(r)
        return x[:self.branch_size] + ls

    def generate_objective_function(self):
        terms = []
        for r in range(self.nrounds):
            terms.extend(self.active_sbox_vars(r))
        return " + ".join(terms) if terms else "0"

    def exclude_trivial_solution(self):
        x0 = self.generate_state_variables(0)
        return [" + ".join(x0) + " >= 1"]

    def generate_constraints(self, ul=None):
        """
        Generate the propagation constraints of the truncated differential model.

        This mirrors the lblock.truncdiff entry point while keeping Splight's two
        S-box layers and linear layer.
        """
        constraints = []
        for r in range(self.nrounds):
            constraints.extend(self.generate_round_constraints_forward_trunc_xor(r))
        return constraints

    def declare_binary_vars(self):
        self.milp_variables = self.ordered_set(self.milp_variables)
        return "Binary\n" + "\n".join(self.milp_variables)

    def make_model(self):
        constraints = self.generate_constraints()
        constraints.extend(self.exclude_trivial_solution())
        lp = "\\ Splight nibble truncated differential model\n"
        lp += "Minimize\n" + self.generate_objective_function() + "\n"
        lp += "Subject To\n" + "\n".join(constraints) + "\n"
        lp += self.declare_binary_vars() + "\nEnd\n"
        with open(self.lp_file_name, "w", encoding="utf-8") as handle:
            handle.write(lp)
        return self.lp_file_name

    def solve(self):
        if read is None:
            raise RuntimeError("gurobipy is required to solve the MILP")
        self.make_model()
        self.milp_model = read(self.lp_file_name)
        self.milp_model.Params.OutputFlag = False
        if self.time_limit is not None:
            self.milp_model.Params.TimeLimit = self.time_limit
        self.milp_model.optimize()
        status = self.milp_model.Status
        objective = None
        if status in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.INTERRUPTED) and self.milp_model.SolCount:
            objective = self.milp_model.objVal
        return {"status": int(status), "objective": objective, "lp_file": self.lp_file_name}
