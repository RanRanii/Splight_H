from splight.sbox_constraints import (
    SBOX_INEQUALITIES,
    compute_ddt,
    ddt_support_points,
    ddt_weight,
    instantiate_sbox_inequalities,
    weight_table,
)
from splight.splight_spec import SBOX_SPLIGHT_S3


def test_sbox_is_permutation_and_ddt_rows_sum_to_16():
    assert sorted(SBOX_SPLIGHT_S3) == list(range(16))
    ddt = compute_ddt(SBOX_SPLIGHT_S3)
    assert all(sum(row) == 16 for row in ddt)
    assert ddt[0][0] == 16
    assert all(ddt[0][dy] == 0 for dy in range(1, 16))


def test_support_points_match_nonzero_ddt():
    ddt = compute_ddt(SBOX_SPLIGHT_S3)
    points = ddt_support_points(SBOX_SPLIGHT_S3)
    assert len(points) == sum(1 for dx in range(16) for dy in range(16) if ddt[dx][dy])
    assert all(len(point) == 8 for point in points)


def test_weight_table():
    weights = weight_table(SBOX_SPLIGHT_S3)
    assert weights[0][0] == 0
    assert ddt_weight(1, 1) == 2
    assert weights[1][2] is None


def test_fixed_sbox_inequalities_are_instantiated():
    constraints = instantiate_sbox_inequalities(
        ["x0", "x1", "x2", "x3"],
        ["y0", "y1", "y2", "y3"],
        ["p0", "p1", "p2"],
    )
    assert len(SBOX_INEQUALITIES) == 25
    assert len(constraints) == 25
    assert "a0" not in " ".join(constraints)
    assert "b0" not in " ".join(constraints)
    assert "pr0" not in " ".join(constraints)
    assert "p1 - p2 >= 0" in constraints
    assert "- p1 + p2 >= 0" in constraints
