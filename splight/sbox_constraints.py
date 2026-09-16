"""DDT helpers and fixed S-box MILP inequalities for Splight S3."""

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from splight.splight_spec import SBOX_SPLIGHT_S3, nibble_to_bits

VARIABLE_ORDER = ["di0", "di1", "di2", "di3", "do0", "do1", "do2", "do3"]

SBOX_INEQUALITIES = [
    "- a0 - 2*a1 - a2 - a3 + b1 + b2 + pr0 + 3*pr2 >= 0",
    "- a0 - 3*a1 - a2 - a3 - b0 - b1 - b2 + 2*pr0 + 6*pr2 >= 0",
    "- a0 + a1 - a2 - a3 - b0 - pr0 + 4*pr2 >= 0",
    "- a0 - a1 + a3 - 3*b0 - 2*b1 + b2 - b3 + 3*pr0 + 4*pr2 >= 0",
    "- a0 - a1 + a2 - 3*b0 - b1 - b3 + 2*pr0 + 4*pr2 >= 0",
    "a0 - a1 - a2 - a3 - 2*b0 - b1 + b2 - b3 + 3*pr0 + 3*pr2 >= 0",
    "a1 + b3 - pr0 >= 0",
    "a0 - a1 - a3 - 2*b0 + b1 - b2 - b3 + 2*pr0 + 3*pr2 >= 0",
    "- a0 - a1 - 2*a2 + a3 - 2*b0 + b1 - b2 - 2*b3 + 4*pr0 + 4*pr2 >= 0",
    "- 3*a1 - a2 - b0 - b1 - b2 + b3 + 2*pr0 + 4*pr2 >= 0",
    "a1 - a3 + b0 + b3 >= 0",
    "- a0 + a2 + b0 + b1 >= 0",
    "a0 - a1 + a3 - 2*b0 - b1 + b2 - 2*b3 + 4*pr0 + 2*pr2 >= 0",
    "3*a0 - 6*a1 - a2 - 3*a3 - b0 - 3*b1 - 7*b2 + b3 + 8*pr0 + 12*pr2 >= 0",
    "a1 + 2*a3 + 2*b0 - b2 - b3 - pr0 + pr2 >= 0",
    "a0 - a2 + a3 - b0 - b1 - b2 - pr0 + 4*pr2 >= 0",
    "2*a0 + 2*a1 + a2 + a3 - pr0 - pr2 >= 0",
    "a0 + 2*a2 + 2*b1 + b3 - pr0 - pr2 >= 0",
    "a0 + a3 - b0 + b1 + b2 - 2*b3 + pr0 + pr2 >= 0",
    "b0 + b1 + 2*b2 + 2*b3 - pr0 - pr2 >= 0",
    "a2 + b0 - pr0 >= 0",
    "- a1 - 3*a2 - a3 + 2*b0 + b2 + 2*pr0 + 2*pr2 >= 0",
    "- a0 + 2*a1 + 2*a2 + a3 - b0 - 3*b1 + 3*pr0 + 2*pr2 >= 0",
    "pr1 - pr2 >= 0",
    "- pr1 + pr2 >= 0",
]


def instantiate_sbox_inequalities(input_bits, output_bits, probability_bits):
    """Substitute concrete MILP variable names into the fixed S-box inequalities."""
    if len(input_bits) != 4 or len(output_bits) != 4 or len(probability_bits) != 3:
        raise ValueError("S-box constraints require 4 input bits, 4 output bits, and 3 pr bits")
    constraints = []
    for inequality in SBOX_INEQUALITIES:
        replaced = inequality
        for bit in range(4):
            replaced = replaced.replace(f"a{bit}", input_bits[bit])
            replaced = replaced.replace(f"b{bit}", output_bits[bit])
        for bit in range(3):
            replaced = replaced.replace(f"pr{bit}", probability_bits[bit])
        constraints.append(replaced.replace("*", " "))
    return constraints


def compute_ddt(sbox):
    if sorted(sbox) != list(range(16)):
        raise ValueError("S-box must be a 4-bit permutation")
    ddt = [[0 for _ in range(16)] for _ in range(16)]
    for dx in range(16):
        for x in range(16):
            ddt[dx][sbox[x] ^ sbox[x ^ dx]] += 1
    return ddt


def ddt_support_points(sbox, bit_order="msb"):
    ddt = compute_ddt(sbox)
    points = []
    for dx in range(16):
        for dy in range(16):
            if ddt[dx][dy]:
                points.append(nibble_to_bits(dx, bit_order) + nibble_to_bits(dy, bit_order))
    return points


def ddt_weight(dx, dy, sbox=SBOX_SPLIGHT_S3):
    count = compute_ddt(sbox)[dx][dy]
    if count == 0:
        return math.inf
    return int(round(-math.log2(count / 16)))


def weight_table(sbox=SBOX_SPLIGHT_S3):
    ddt = compute_ddt(sbox)
    return [[None if ddt[dx][dy] == 0 else int(round(-math.log2(ddt[dx][dy] / 16)))
             for dy in range(16)] for dx in range(16)]


def generate_convex_hull_inequalities(points):
    """Return Sage-generated inequalities if SageMath is installed.

    Inequality rows are dictionaries with coefficients in VARIABLE_ORDER and a
    constant. If Sage is unavailable, return [] and let callers use the exact
    forbidden-point encoding generated from the DDT support.
    """
    sage = shutil.which("sage")
    if sage is None:
        return []
    script = """
import json
from sage.all import Polyhedron
points = json.loads(open(INPUT).read())
poly = Polyhedron(vertices=points)
rows = []
for ineq in poly.inequalities():
    vals = list(ineq)
    rows.append({"constant": int(vals[0]), "coefficients": [int(v) for v in vals[1:]]})
open(OUTPUT, "w").write(json.dumps(rows, indent=2))
"""
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "points.json"
        out_path = Path(tmp) / "ineq.json"
        py_path = Path(tmp) / "gen.sage.py"
        in_path.write_text(json.dumps(points), encoding="utf-8")
        py_path.write_text(
            f"INPUT={str(in_path)!r}\nOUTPUT={str(out_path)!r}\n" + script,
            encoding="utf-8",
        )
        subprocess.run([sage, str(py_path)], check=True, capture_output=True, text=True)
        return json.loads(out_path.read_text(encoding="utf-8"))


def invalid_transition_constraints(input_bits, output_bits, sbox=SBOX_SPLIGHT_S3, bit_order="msb"):
    """Exact binary DDT support constraints by excluding invalid 8-bit points."""
    ddt = compute_ddt(sbox)
    constraints = []
    for dx in range(16):
        for dy in range(16):
            if ddt[dx][dy] != 0:
                continue
            point = nibble_to_bits(dx, bit_order) + nibble_to_bits(dy, bit_order)
            variables = list(input_bits) + list(output_bits)
            terms = []
            for var, bit in zip(variables, point):
                terms.append(var if bit == 0 else f"- {var}")
            rhs = 7 - sum(point)
            constraints.append(" + ".join(terms).replace("+ -", "- ") + f" <= {rhs}")
    return constraints


def save_inequalities(path):
    path = Path(path)
    points = ddt_support_points(SBOX_SPLIGHT_S3, bit_order="msb")
    data = {
        "sbox": SBOX_SPLIGHT_S3,
        "variable_order": VARIABLE_ORDER,
        "bit_order": "msb",
        "ddt": compute_ddt(SBOX_SPLIGHT_S3),
        "support_points": points,
        "convex_hull_inequalities": generate_convex_hull_inequalities(points),
        "weight_table": weight_table(SBOX_SPLIGHT_S3),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def load_inequalities(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
