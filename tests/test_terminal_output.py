import sys

import boom


def _params():
    return {
        "r0": 2,
        "rm": 3,
        "r1": 2,
        "w0": 6,
        "wm": 3,
        "w1": 6,
        "timelimit": 1200.0,
    }


def test_terminal_output_is_mirrored_and_streams_are_restored(tmp_path, capsys):
    log_path = tmp_path / "terminal_print.txt"
    stdout_before = sys.stdout
    stderr_before = sys.stderr

    with boom._tee_terminal_output(log_path):
        print("stdout marker")
        print("stderr marker", file=sys.stderr)

    assert sys.stdout is stdout_before
    assert sys.stderr is stderr_before
    captured = capsys.readouterr()
    assert "stdout marker" in captured.out
    assert "stderr marker" in captured.err
    assert log_path.read_text(encoding="utf-8") == "stdout marker\nstderr marker\n"


class _FakeTruncatedBoomerang:
    def __init__(self):
        self.limit = None

    def find_truncated_boomerang_trail(self, time_limit_override=None):
        self.limit = time_limit_override
        print("GUROBI TRUNCATED OPTIMIZER")
        print("Number of active S-boxes: 7.0")
        print("Total time to find the trail: 0.01 seconds")
        return {"status": "OPTIMAL", "solution": {"ok": True}}

    def parse_solver_output(self):
        print("Upper Truncated Trail:")
        print("Lower Truncated Trail:")
        print("Middle Part:")
        return {}, {}, {}

    def extract_current_truncated_path(self):
        return {
            "activity": {
                "upper_trail": {},
                "middle_part": {"as": 0},
                "lower_trail": {},
            }
        }


class _FakeDiff:
    def __init__(self, side):
        self.side = side
        self.last_solve_status = "OPTIMAL"

    def find_characteristic(self, time_limit_override=None):
        assert time_limit_override == 1200.0
        print(f"{self.side} differential characteristic")
        return {"total_weight": 2.0}


def test_legacy_terminal_phases_follow_reference_order(monkeypatch, capsys):
    bm = _FakeTruncatedBoomerang()
    monkeypatch.setattr(boom, "_prepare_truncated_model", lambda _params: bm)
    monkeypatch.setattr(
        boom,
        "_build_diff_for_truncated_side",
        lambda _bm, _snapshot, _params, side, _deadline: (
            _FakeDiff(side),
            [],
            {},
        ),
    )

    result = boom._run_legacy_search(_params(), None)

    assert result["result"] == "SAT"
    assert bm.limit == 1200.0
    output = capsys.readouterr().out
    markers = [
        "GUROBI TRUNCATED OPTIMIZER",
        "Number of active S-boxes",
        "Truncated trail search time",
        "Upper Truncated Trail:",
        "Lower Truncated Trail:",
        "Middle Part:",
        "upper differential characteristic",
        "Upper Diff instantiation time",
        "lower differential characteristic",
        "Lower Diff instantiation time",
    ]
    positions = [output.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_legacy_search_preserves_stopped_status(monkeypatch):
    bm = _FakeTruncatedBoomerang()
    bm.find_truncated_boomerang_trail = lambda **_kwargs: {
        "status": "TIME_LIMIT",
        "solution": None,
    }
    monkeypatch.setattr(boom, "_prepare_truncated_model", lambda _params: bm)

    result = boom._run_legacy_search(_params(), None)

    assert result["result"] == "TIME_LIMIT"


def test_zero_round_sides_report_no_instantiation(capsys):
    params = _params()
    params.update({"r0": 0, "rm": 1, "r1": 0})
    snapshot = {"activity": {"middle_part": {"as": 0}}}

    boom._print_search_summary(params, None, None, snapshot)

    output = capsys.readouterr().out
    assert output.count("0-round side; no concrete instantiation required") == 2
    assert "Boomerang distinguisher for 0 + 1 + 0" in output
