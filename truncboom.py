"""Hadipour three-part truncated sandwich/boomerang model for Splight."""

import json
import os
import time

try:
    from gurobipy import GRB, quicksum, read
except Exception:  # pragma: no cover
    GRB = None
    read = None

from truncdiff import TruncDiff


TMP_DIR = "tmp"


class TruncatedBoomerang:
    def __init__(self, r0, rm, r1, w0, wm, w1, time_limit=None):
        self.r0 = int(r0)
        self.rm = int(rm)
        self.r1 = int(r1)
        self.R0 = self.r0 + self.rm
        self.R1 = self.rm + self.r1
        self.w0 = int(w0)
        self.wm = int(wm)
        self.w1 = int(w1)
        self.time_limit = time_limit
        self.milp_variables = []
        os.makedirs(TMP_DIR, exist_ok=True)
        self.lp_file_name = os.path.join(
            TMP_DIR,
            f"splight_h_{self.r0}_{self.rm}_{self.r1}.lp",
        )
        self.upper = TruncDiff(self.R0, prefix="u")
        self.lower = TruncDiff(self.R1, prefix="v")
        self.milp_model = None
        self.solution = {}
        self.truncated_nogood_count = 0

    @staticmethod
    def ordered_set(seq):
        seen = set()
        out = []
        for item in seq:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def _collect(self):
        self.milp_variables = self.ordered_set(
            self.milp_variables + self.upper.milp_variables + self.lower.milp_variables
        )

    def constraint_by_xor(self, a, b, c):
        """
        LBlock-compatible probability-one truncated XOR template.

        The concrete Splight round generation delegates to TruncDiff, but this
        method keeps the same top-level helper available in truncboom.py.
        """
        return [
            f"{c} - {a} >= 0",
            f"{c} - {b} >= 0",
            f"{a} + {b} - {c} >= 0",
        ]

    def constraint_by_trunc_xor(self, a, b, c):
        return [
            f"{a} + {b} - {c} >= 0",
            f"{a} - {b} + {c} >= 0",
            f"- {a} + {b} + {c} >= 0",
        ]

    def generate_upper_constraints(self):
        constraints = []
        for r in range(self.R0):
            if r < self.r0:
                constraints.extend(self.upper.generate_round_constraints_forward_trunc_xor(r))
            else:
                constraints.extend(self.upper.generate_round_constraints_forward_xor(r))
        self._collect()
        return constraints

    def generate_lower_constraints(self):
        constraints = []
        for r in range(self.R1):
            if r < self.rm:
                constraints.extend(self.lower.generate_round_constraints_backward_xor(r))
            else:
                constraints.extend(self.lower.generate_round_constraints_forward_trunc_xor(r))
        self._collect()
        return constraints

    def generate_common_active_variables(self, r):
        ss = [f"s_{r}_{j}" for j in range(16)]
        self.milp_variables.extend(ss)
        return ss

    def generate_common_active_constraints(self):
        constraints = []
        for r in range(self.rm):
            ss = self.generate_common_active_variables(r)
            upper_vars = self.upper.active_sbox_vars(self.r0 + r)
            lower_vars = self.lower.active_sbox_vars(r)
            for u, v, s in zip(upper_vars, lower_vars, ss):
                constraints.append(f"{u} - {s} >= 0")
                constraints.append(f"{v} - {s} >= 0")
                constraints.append(f"- {u} - {v} + {s} >= -1")
        self._collect()
        return constraints

    def generate_objective_function(self):
        terms = []
        for r in range(self.r0):
            terms.extend(f"{self.w0} {v}" for v in self.upper.active_sbox_vars(r))
        for r in range(self.rm):
            terms.extend(f"{self.wm} {v}" for v in self.generate_common_active_variables(r))
        for r in range(self.rm, self.R1):
            terms.extend(f"{self.w1} {v}" for v in self.lower.active_sbox_vars(r))
        self._collect()
        return " + ".join(terms) if terms else "0"

    def exclude_trivial_solution(self):
        ux0 = self.upper.generate_state_variables(0)
        vx0 = self.lower.generate_state_variables(0)
        self._collect()
        return [" + ".join(ux0) + " >= 1", " + ".join(vx0) + " >= 1"]

    def declare_binary_vars(self):
        self._collect()
        self.milp_variables = self.ordered_set(self.milp_variables)
        return "Binary\n" + "\n".join(self.milp_variables)

    def make_model(self):
        """
        Generate the MILP model in the same top-level order as lblock:
        objective, upper constraints, lower constraints, linking constraints,
        trivial-solution exclusion, binary declarations, end.
        """
        constraints = []
        constraints.extend(self.generate_upper_constraints())
        constraints.extend(self.generate_lower_constraints())
        constraints.extend(self.generate_common_active_constraints())
        constraints.extend(self.exclude_trivial_solution())
        lp = "\\ Splight Hadipour truncated boomerang/sandwich model\n"
        lp += "Minimize\n" + self.generate_objective_function() + "\n"
        lp += "Subject To\n" + "\n".join(constraints) + "\n"
        lp += self.declare_binary_vars() + "\nEnd\n"
        with open(self.lp_file_name, "w", encoding="utf-8") as handle:
            handle.write(lp)
        return self.lp_file_name

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
        self.truncated_nogood_count = 0
        return self.milp_model

    def optimize_next_truncated_path(self, output_flag=False, time_limit_override=None):
        """Optimize the loaded truncated model without rebuilding it."""
        if self.milp_model is None:
            self.load_model()
        self.milp_model.Params.OutputFlag = bool(output_flag)
        effective_time_limit = (
            time_limit_override if time_limit_override is not None else self.time_limit
        )
        if effective_time_limit is not None:
            self.milp_model.Params.TimeLimit = max(float(effective_time_limit), 1e-3)
        started = time.time()
        self.milp_model.optimize()
        elapsed = time.time() - started
        status = self._gurobi_status_name(self.milp_model.Status)
        if not self.milp_model.SolCount:
            result = {
                "status": status,
                "solution": None,
                "objective": None,
                "elapsed_seconds": elapsed,
                "lp_file": self.lp_file_name,
            }
        else:
            result = {
                "status": status,
                "solution": self.parse_solution(),
                "objective": float(self.milp_model.ObjVal),
                "elapsed_seconds": elapsed,
                "lp_file": self.lp_file_name,
            }
        self.solution = result
        return result

    def solve(self):
        self.make_model()
        self.load_model()
        self.milp_model.Params.PoolSearchMode = 2
        self.milp_model.Params.PoolSolutions = 10
        return self.optimize_next_truncated_path(output_flag=False)

    def find_truncated_boomerang_trail(self, time_limit_override=None):
        """Reference-compatible solver entry used by boom.py."""
        if read is None:
            raise RuntimeError("gurobipy is required to solve the MILP")
        if self.milp_model is None:
            self.make_model()
            self.load_model()
        self.milp_model.Params.PoolSearchMode = 2
        self.milp_model.Params.PoolSolutions = 10
        self.milp_model.Params.SolutionNumber = 0
        result = self.optimize_next_truncated_path(
            output_flag=True,
            time_limit_override=time_limit_override,
        )
        if result["solution"] is not None:
            print(f"Number of active S-boxes: {result['objective']}")
        else:
            print("No feasible truncated boomerang trail found")
        print("Total time to find the trail: %0.02f seconds" % result["elapsed_seconds"])
        return result

    def _var_value(self, name):
        var = self.milp_model.getVarByName(name)
        if var is None:
            raise ValueError(f"unknown truncated Gurobi variable: {name}")
        value = int(round(var.X))
        if value not in (0, 1) or abs(var.X - value) > 1e-5:
            raise RuntimeError(f"non-binary value {var.X!r} for truncated variable {name}")
        return value

    @staticmethod
    def _is_truncated_activity_name(name):
        """Keep state activity bits and omit temporary truncated-XOR helpers."""
        if name.startswith(("ux_", "uy_", "ul_", "uz_")):
            return True
        if name.startswith(("vx_", "vy_", "vl_", "vz_")):
            return True
        return name.startswith("s_")

    def truncated_solution_signature(self):
        if self.milp_model is None or not self.milp_model.SolCount:
            raise RuntimeError("no current truncated Gurobi solution is available")
        assignment = []
        for var in sorted(self.milp_model.getVars(), key=lambda item: item.VarName):
            if self._is_truncated_activity_name(var.VarName):
                assignment.append((var.VarName, self._var_value(var.VarName)))
        if not assignment:
            raise RuntimeError("the truncated activity signature is empty")
        return assignment

    def extract_current_truncated_path(self):
        """Freeze the active path before another optimize changes Var.X."""
        if self.milp_model is None or not self.milp_model.SolCount:
            raise RuntimeError("no current truncated Gurobi solution is available")
        return {
            "objective": float(self.milp_model.ObjVal),
            "signature": self.truncated_solution_signature(),
            "activity": self.parse_solution(),
            "truncated_model_id": id(self.milp_model),
        }

    def add_truncated_path_nogood(self, solution, truncated_id=None, constraint_name=None):
        """Block exactly one full truncated activity pattern."""
        if self.milp_model is None:
            raise RuntimeError("the truncated Gurobi model has not been loaded")
        assignment = solution.get("signature", solution)
        if not assignment:
            raise ValueError("cannot add an empty truncated-path no-good")
        terms = []
        for var_name, value in assignment:
            var = self.milp_model.getVarByName(var_name)
            if var is None:
                raise ValueError(f"unknown truncated variable in no-good: {var_name}")
            if value == 0:
                terms.append(var)
            elif value == 1:
                terms.append(1 - var)
            else:
                raise ValueError(f"invalid binary no-good value {value!r} for {var_name}")
        self.truncated_nogood_count += 1
        number = truncated_id if truncated_id is not None else self.truncated_nogood_count
        name = constraint_name or f"truncated_nogood_{int(number):04d}"
        self.milp_model.addConstr(quicksum(terms) >= 1, name=name)
        self.milp_model.update()
        return name

    def parse_solution(self):
        if self.milp_model is None or not self.milp_model.SolCount:
            return {}
        upper_trail = {}
        lower_trail = {}
        middle = {}
        for r in range(self.R0 + 1):
            names = self.upper.generate_state_variables(r)
            upper_trail[f"x_{r}"] = "".join(str(self._var_value(name)) for name in names)
            if r < self.R0:
                ys, ls, zs = self.upper.generate_round_variables(r)
                upper_trail[f"y_{r}"] = "".join(str(self._var_value(name)) for name in ys)
                upper_trail[f"l_{r}"] = "".join(str(self._var_value(name)) for name in ls)
                upper_trail[f"z_{r}"] = "".join(str(self._var_value(name)) for name in zs)
            else:
                upper_trail[f"y_{r}"] = "none"
                upper_trail[f"l_{r}"] = "none"
                upper_trail[f"z_{r}"] = "none"
        for r in range(self.R1 + 1):
            names = self.lower.generate_state_variables(r)
            lower_trail[f"x_{r}"] = "".join(str(self._var_value(name)) for name in names)
            if r < self.R1:
                ys, ls, zs = self.lower.generate_round_variables(r)
                lower_trail[f"y_{r}"] = "".join(str(self._var_value(name)) for name in ys)
                lower_trail[f"l_{r}"] = "".join(str(self._var_value(name)) for name in ls)
                lower_trail[f"z_{r}"] = "".join(str(self._var_value(name)) for name in zs)
            else:
                lower_trail[f"y_{r}"] = "none"
                lower_trail[f"l_{r}"] = "none"
                lower_trail[f"z_{r}"] = "none"
        common_count = 0
        for r in range(self.rm):
            names = self.generate_common_active_variables(r)
            vals = [self._var_value(name) for name in names]
            common_count += sum(vals)
            middle[f"s_{r}"] = "".join(str(v) for v in vals)
        middle["common_active_sboxes"] = common_count
        middle["as"] = common_count
        return {"upper_trail": upper_trail, "middle_part": middle, "lower_trail": lower_trail}

    def print_truncated_solution(self, parsed):
        """Print one extracted truncated path without re-reading the model."""
        self.upper_trail = parsed.get("upper_trail", {})
        self.middle_part = parsed.get("middle_part", {})
        self.lower_trail = parsed.get("lower_trail", {})

        print("\nUpper Truncated Trail:\n")
        print("Rounds  x                 y         l         z        ")
        print("-" * 56)
        for r in range(self.R0 + 1):
            print(
                f"{r:<7} "
                f"{self.upper_trail.get(f'x_{r}', ''):<17} "
                f"{self.upper_trail.get(f'y_{r}', 'none'):<9} "
                f"{self.upper_trail.get(f'l_{r}', 'none'):<9} "
                f"{self.upper_trail.get(f'z_{r}', 'none'):<9}"
            )
        print("\n%s\n%s" % ("+" * 16, "#" * 16))
        print("Lower Truncated Trail:\n")
        print("Rounds  x                 y         l         z        ")
        print("-" * 56)
        for r in range(self.R1 + 1):
            print(
                f"{r:<7} "
                f"{self.lower_trail.get(f'x_{r}', ''):<17} "
                f"{self.lower_trail.get(f'y_{r}', 'none'):<9} "
                f"{self.lower_trail.get(f'l_{r}', 'none'):<9} "
                f"{self.lower_trail.get(f'z_{r}', 'none'):<9}"
            )
        print("\n%s\n%s" % ("#" * 16, "#" * 16))
        print("Middle Part:\n")
        for r in range(self.rm):
            value = self.middle_part.get(f"s_{r}", "")
            print("*".join(value) + "*" if value else "")
        print(f"\nNumber of common active S-boxes: {self.middle_part.get('as', 0)}")
        return self.upper_trail, self.middle_part, self.lower_trail

    def parse_solver_output(self):
        """Extract and print the current truncated solution."""
        return self.print_truncated_solution(self.parse_solution())

    def save_result(self, path):
        data = self.solution or {"lp_file": self.lp_file_name}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        return data


TruncatedBoomerangH = TruncatedBoomerang
