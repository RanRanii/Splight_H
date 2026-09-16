import json

import pytest
import boom

from diff import Diff
from tools.exact_verify import verify_fixed_differences
from truncboom import TruncatedBoomerang


def test_same_key_exact_verifier_zero_difference_replays():
    result = verify_fixed_differences(
        rounds=1,
        round_offset=0,
        fixed_diffs={
            "dX0": "0000000000000000",
            "dY0": "00000000",
            "dLIN0": "00000000",
            "dAK0": "00000000",
            "dZ0": "00000000",
            "dX1": "0000000000000000",
        },
    )
    assert result["status"] == "SAT"
    assert result["witness"]["concrete_replay"] == "PASS"


def test_diff_full_trail_nogood_changes_signature():
    pytest.importorskip("gurobipy")
    diff = Diff({"nrounds": 1, "timelimit": 20})
    diff.make_model()
    diff.load_model()
    first = diff.optimize_next_candidate()
    if first["status"] not in {"OPTIMAL", "TIME_LIMIT_WITH_SOLUTION"}:
        pytest.skip(f"Gurobi did not return a candidate: {first['status']}")
    solution = diff.extract_solution_for_exact_verify()
    signature = solution["nogood_assignment"]
    weight = solution["weight"]
    diff.add_solution_nogood(solution, "test_exact_nogood")
    second = diff.optimize_next_candidate()
    assert second["status"] in {"OPTIMAL", "TIME_LIMIT_WITH_SOLUTION", "INFEASIBLE"}
    if second["status"] != "INFEASIBLE":
        assert diff.concrete_solution_signature() != signature
        assert diff.milp_model.ObjVal >= weight


def test_truncated_full_path_nogood_changes_signature():
    pytest.importorskip("gurobipy")
    model = TruncatedBoomerang(1, 1, 1, 4, 2, 4, time_limit=20)
    model.make_model()
    model.load_model()
    first = model.optimize_next_truncated_path()
    if first["status"] not in {"OPTIMAL", "TIME_LIMIT_WITH_SOLUTION"}:
        pytest.skip(f"Gurobi did not return a truncated path: {first['status']}")
    snapshot = model.extract_current_truncated_path()
    model.add_truncated_path_nogood(snapshot, constraint_name="test_truncated_nogood")
    second = model.optimize_next_truncated_path()
    assert second["status"] in {"OPTIMAL", "TIME_LIMIT_WITH_SOLUTION", "INFEASIBLE"}
    if second["status"] != "INFEASIBLE":
        assert model.truncated_solution_signature() != snapshot["signature"]


class _FakeTruncatedModel:
    def __init__(self):
        self.position = 0
        self.added = []

    def optimize_next_truncated_path(self, **_kwargs):
        self.position += 1
        if self.position <= 2:
            return {"status": "OPTIMAL", "solution": {"raw": self.position}}
        return {"status": "INFEASIBLE", "solution": None}

    def extract_current_truncated_path(self):
        return {
            "signature": [(f"activity_{self.position}", 1)],
            "activity": {
                "upper_trail": {},
                "middle_part": {},
                "lower_trail": {},
            },
        }

    def add_truncated_path_nogood(self, _snapshot, name):
        self.added.append(name)
        return name


class _FakeDiff:
    def optimize_next_candidate(self, **_kwargs):
        return {"status": "OPTIMAL", "solution": {}}


def _exact_params():
    return {
        "r0": 1, "rm": 1, "r1": 1, "w0": 4, "wm": 2, "w1": 4,
        "timelimit": 1, "exact_verify_timeout_ms": 1,
        "exact_path_timeout_sec": None,
    }


def _sat_result(side):
    return {
        "result": "SAT",
        "candidate_count": 1,
        "rejected_candidates": 0,
        "unknown_candidates": 0,
        "accepted": {
            "weight": 0,
            "characteristic": {
                "nrounds": 1,
                "total_weight": "0",
                "x_0": "0000000000000000",
                "x_1": "0000000000000000",
            },
            "side": side,
        },
    }


def test_outer_loop_reuses_truncated_model_after_concrete_exhaustion(tmp_path, monkeypatch):
    model = _FakeTruncatedModel()
    monkeypatch.setattr(boom, "_prepare_truncated_model", lambda _params: model)
    monkeypatch.setattr(
        boom,
        "_build_diff_for_truncated_side",
        lambda *_args: (_FakeDiff(), [], {}),
    )

    def refine(_diff, _expected, _initial, *, side, truncated_id, **_kwargs):
        return _sat_result(side) if truncated_id == 2 else {
            "result": "CONCRETE_EXHAUSTED",
            "candidate_count": 1,
            "rejected_candidates": 1,
            "unknown_candidates": 0,
        }

    monkeypatch.setattr(boom, "_refine_diff_until_exact_sat", refine)
    result = boom._run_exact_two_level_search(_exact_params(), tmp_path, None)
    assert result["result"] == "SAT"
    assert result["summary"]["accepted_truncated_id"] == 2
    assert model.added == ["truncated_nogood_0001"]
    assert (tmp_path / "truncated_0001").is_dir()
    assert (tmp_path / "truncated_0002").is_dir()


def test_outer_loop_marks_timeout_skipped_without_concrete_nogood(tmp_path, monkeypatch):
    model = _FakeTruncatedModel()
    monkeypatch.setattr(boom, "_prepare_truncated_model", lambda _params: model)
    monkeypatch.setattr(
        boom,
        "_build_diff_for_truncated_side",
        lambda *_args: (_FakeDiff(), [], {}),
    )

    def refine(_diff, _expected, _initial, *, side, truncated_id, **_kwargs):
        if truncated_id == 1:
            return {
                "result": "TIMEOUT_SKIPPED",
                "reason": "Z3_TIMEOUT",
                "candidate_count": 1,
                "rejected_candidates": 0,
                "unknown_candidates": 1,
            }
        return _sat_result(side)

    monkeypatch.setattr(boom, "_refine_diff_until_exact_sat", refine)
    result = boom._run_exact_two_level_search(_exact_params(), tmp_path, None)
    assert result["result"] == "SAT"
    assert model.added == ["truncated_nogood_0001"]
    first = json.loads((tmp_path / "truncated_0001" / "summary.json").read_text())
    assert first["result"] == "TIMEOUT_SKIPPED"
