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

    def print_truncated_solution(self, _activity):
        print("Upper Truncated Trail:")
        print(f"fake upper path {self.position}")
        print("Lower Truncated Trail:")
        print(f"fake lower path {self.position}")
        print("Middle Part:")
        print(f"fake middle path {self.position}")

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


def test_instantiation_lp_cleaner_only_deletes_registered_current_run_files(tmp_path):
    root = tmp_path / "boomerang"
    root.mkdir()
    historical = root / "splight_h_diff_100_1000.lp"
    historical.write_text("historical", encoding="utf-8")
    current = root / "splight_h_diff_200_2000.lp"
    current.write_text("current", encoding="utf-8")
    outside = tmp_path / "splight_h_diff_300_3000.lp"
    outside.write_text("outside", encoding="utf-8")

    cleaner = boom._InstantiationLpCleaner(root=root)
    historical_diff = type(
        "Diff", (), {"lp_file_name": str(historical), "milp_model": None}
    )()
    current_diff = type(
        "Diff", (), {"lp_file_name": str(current), "milp_model": None}
    )()
    outside_diff = type(
        "Diff", (), {"lp_file_name": str(outside), "milp_model": None}
    )()

    assert cleaner.register(current_diff) is True
    assert cleaner.register(outside_diff) is False
    assert cleaner.delete(historical_diff, "must_not_touch_history") is False
    assert cleaner.delete(current_diff, "definitively_rejected") is True

    assert historical.exists()
    assert outside.exists()
    assert not current.exists()
    snapshot = cleaner.snapshot()
    assert snapshot["registered_files"] == 1
    assert snapshot["deleted_files"] == 1
    assert snapshot["deleted_bytes"] == len("current")
    assert snapshot["refused_files"] == 1


def test_raw_rejected_candidate_deletes_both_new_instantiation_lps(
    tmp_path, monkeypatch
):
    root = tmp_path / "boomerang"
    root.mkdir()
    historical = root / "splight_h_diff_999_9999.lp"
    historical.write_text("historical", encoding="utf-8")
    cleaner = boom._InstantiationLpCleaner(root=root)
    created_paths = []
    model = _FakeTruncatedModel()
    model.position = 1

    def one_raw_candidate(**_kwargs):
        if model.position == 1:
            model.position = 2
            return {"status": "OPTIMAL", "solution": {"raw": 1}}
        return {"status": "INFEASIBLE", "solution": None}

    model.optimize_next_truncated_path = one_raw_candidate
    model.extract_current_truncated_path = lambda: {
        "signature": [("activity_1", 1)],
        "activity": {"upper_trail": {}, "middle_part": {}, "lower_trail": {}},
    }
    monkeypatch.setattr(boom, "_prepare_truncated_model", lambda _params: model)

    def builder(_bm, _snapshot, _params, side, _deadline):
        index = len(created_paths) + 1
        path = root / f"splight_h_diff_123_{index}.lp"
        path.write_text(side, encoding="utf-8")
        created_paths.append(path)
        diff = _FakeDiff()
        diff.lp_file_name = str(path)
        diff.milp_model = None
        if side == "lower":
            diff.optimize_next_candidate = lambda **_kwargs: {
                "status": "INFEASIBLE",
                "solution": None,
            }
        return diff, [], {}

    monkeypatch.setattr(boom, "_build_diff_for_truncated_side", builder)
    result = boom._run_exact_two_level_search(
        _exact_params(), tmp_path / "results", None, lp_cleaner=cleaner
    )

    assert result["result"] == "NO_EXACT_REALIZABLE_TRAIL"
    assert result["instantiation_lp_cleanup"]["deleted_files"] == 2
    assert result["instantiation_lp_cleanup"]["retained_registered_files"] == 0
    assert historical.exists()
    assert all(not path.exists() for path in created_paths)


def test_outer_loop_reuses_truncated_model_after_concrete_exhaustion(
    tmp_path, monkeypatch, capsys
):
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
    output = capsys.readouterr().out
    assert output.count("Upper Truncated Trail:") == 2
    assert output.count("Lower Truncated Trail:") == 2
    assert output.count("Middle Part:") == 2
    markers = [
        "Qualified truncated path #1",
        "fake upper path 1",
        "fake lower path 1",
        "fake middle path 1",
        "Qualified truncated path #2",
        "fake upper path 2",
        "fake lower path 2",
        "fake middle path 2",
    ]
    positions = [output.index(marker) for marker in markers]
    assert positions == sorted(positions)


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
