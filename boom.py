#!/usr/bin/env python3
"""
Same-key boomerang/sandwich search for Splight.

Without --exact-verify this keeps the historical one-shot workflow.  With
--exact-verify it refines concrete characteristics with the Z3 verifier and
enumerates a new truncated path only after the current path is exhausted or
skipped because of an exact-verification timeout.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from gurobipy import GRB

from diff import Diff
from output.plotdistinguisher import save_markdown_distinguisher, tex_fin, tex_init, tex_middle
from probability.probability_evaluator import save_probability_checks
from truncboom import TruncatedBoomerang
from tools.exact_verify import verify_trail


PROJECT_DIR = Path(__file__).resolve().parent


class _InstantiationLpCleaner:
    """Delete only rejected Diff LP files created and registered in this run."""

    _LP_NAME = re.compile(r"splight_h_diff_\d+_\d+\.lp")

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        default_root = PROJECT_DIR / "tmp" / "boomerang"
        self.root = os.path.realpath(root or default_root)
        self._owned: dict[str, tuple[int, int, int, int]] = {}
        self._stats = {
            "policy": "delete-definitively-rejected-current-run-only",
            "registered_files": 0,
            "deleted_files": 0,
            "deleted_bytes": 0,
            "refused_files": 0,
            "delete_failures": 0,
        }

    def _resolve_safe_path(self, diff: Any) -> str | None:
        raw_path = getattr(diff, "lp_file_name", None)
        if not raw_path:
            return None
        path = os.path.realpath(raw_path)
        try:
            inside_root = os.path.commonpath((self.root, path)) == self.root
        except ValueError:
            inside_root = False
        if not inside_root or self._LP_NAME.fullmatch(os.path.basename(path)) is None:
            return None
        return path

    @staticmethod
    def _file_identity(stat_result: os.stat_result) -> tuple[int, int, int, int]:
        return (
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_size,
            stat_result.st_mtime_ns,
        )

    def register(self, diff: Any) -> bool:
        """Register a new LP explicitly; historical unregistered files stay untouched."""
        path = self._resolve_safe_path(diff)
        if path is None:
            if getattr(diff, "lp_file_name", None):
                self._stats["refused_files"] += 1
                print(f"LP cleanup registration refused: {diff.lp_file_name}")
            return False
        try:
            stat_result = os.stat(path)
        except OSError as exc:
            self._stats["refused_files"] += 1
            print(f"LP cleanup registration failed: path={path} error={exc}")
            return False
        self._owned[path] = self._file_identity(stat_result)
        self._stats["registered_files"] += 1
        return True

    def delete(self, diff: Any, reason: str) -> bool:
        """Delete a registered LP only if it is still the registered file."""
        path = self._resolve_safe_path(diff)
        if path is None or path not in self._owned:
            return False
        registered_identity = self._owned[path]
        try:
            current_stat = os.stat(path)
            if self._file_identity(current_stat) != registered_identity:
                self._stats["refused_files"] += 1
                print(f"LP cleanup refused changed file: path={path} reason={reason}")
                return False

            model = getattr(diff, "milp_model", None)
            dispose = getattr(model, "dispose", None)
            if callable(dispose):
                dispose()
            if hasattr(diff, "milp_model"):
                diff.milp_model = None

            os.remove(path)
            self._owned.pop(path, None)
            self._stats["deleted_files"] += 1
            self._stats["deleted_bytes"] += current_stat.st_size
            return True
        except FileNotFoundError:
            self._owned.pop(path, None)
            return False
        except OSError as exc:
            self._stats["delete_failures"] += 1
            print(f"LP cleanup warning: path={path} reason={reason} error={exc}")
            return False

    def delete_many(self, diffs: list[Any] | tuple[Any, ...], reason: str) -> dict[str, int]:
        deleted_before = self._stats["deleted_files"]
        bytes_before = self._stats["deleted_bytes"]
        for diff in diffs:
            self.delete(diff, reason)
        return {
            "deleted_files": self._stats["deleted_files"] - deleted_before,
            "deleted_bytes": self._stats["deleted_bytes"] - bytes_before,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            **self._stats,
            "retained_registered_files": len(self._owned),
        }


class _TeeTextStream(io.TextIOBase):
    """Write identical console output to the terminal log."""

    def __init__(self, *streams: io.TextIOBase) -> None:
        self._streams = streams

    def write(self, text: str) -> int:
        for stream in self._streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def result_case_dir_name(params: dict[str, Any]) -> str:
    return (
        f"{params['r0']}-{params['rm']}-{params['r1']}_"
        f"{params['w0']}{params['wm']}{params['w1']}"
    )


@contextlib.contextmanager
def _tee_terminal_output(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as log:
        stdout = _TeeTextStream(sys.stdout, log)
        stderr = _TeeTextStream(sys.stderr, log)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            yield


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding="utf-8")


def _remaining_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _effective_time_limit(params: dict[str, Any], deadline: float | None) -> float:
    remaining = _remaining_seconds(deadline)
    if remaining is None:
        return float(params["timelimit"])
    return min(float(params["timelimit"]), remaining)


def _has_milp_solution(result: dict[str, Any] | None) -> bool:
    if result is None:
        return False
    return result.get("solution") is not None or result.get("status") == "OPTIMAL"


def _format_weight(value: Any) -> str:
    if value is None:
        return "none"
    return f"{float(value):.2f}"


def _trail_markdown(title: str, trail: dict[str, Any]) -> str:
    rows = [
        f"# {title}",
        "",
        f"- Rounds: {trail.get('nrounds', trail.get('rounds', 'none'))}",
        f"- Round offset: {trail.get('round_offset', 0)}",
        f"- Weight: {_format_weight(trail.get('total_weight'))}",
        "",
        "| Round | x | y | l | RK | ak | z | pr |",
        "|---:|---|---|---|---|---|---|---:|",
    ]
    rounds = int(trail.get("nrounds", trail.get("rounds", 0)))
    for r in range(rounds + 1):
        suffix = f"_{r}"
        rows.append(
            "| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} |".format(
                r,
                trail.get(f"x{suffix}", "none"),
                trail.get(f"y{suffix}", "none"),
                trail.get(f"l{suffix}", "none"),
                trail.get(f"rk{suffix}", "00000000" if r < rounds else "none"),
                trail.get(f"ak{suffix}", trail.get(f"l{suffix}", "none")),
                trail.get(f"z{suffix}", "none"),
                trail.get(f"pr{suffix}", "none"),
            )
        )
    return "\n".join(rows) + "\n"


def _truncated_markdown(snapshot: dict[str, Any]) -> str:
    activity = snapshot.get("activity", {})
    lines = [
        "# 截断 boomerang 路径",
        "",
        f"- 截断路径权重: {_format_weight(snapshot.get('weight'))}",
        f"- signature bits: {len(snapshot.get('signature', []))}",
        "",
        "| 变量 | 截断活动值 |",
        "|---|---|",
    ]
    for name in sorted(activity):
        lines.append(f"| {name} | {activity[name]} |")
    return "\n".join(lines) + "\n"


class SupportConsistencyError(RuntimeError):
    pass


def _support(hex_value: str) -> str:
    if hex_value == "none":
        return "none"
    return "".join("0" if item == "0" else "1" for item in hex_value)


def _mask_from_truncated(
    activity: dict[str, Any],
    section: str,
    prefix: str,
    local_round: int,
) -> str:
    value = activity[section].get(f"{prefix}_{local_round}")
    if value is None or value == "none":
        raise ValueError(
            f"missing truncated support for {section}.{prefix}_{local_round}"
        )
    return value


def _truncated_masks(
    activity: dict[str, int],
    side: str,
    rounds: int,
    round_base: int,
    source_base: int,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    section = "upper_trail" if side == "upper" else "lower_trail"
    masks: dict[str, str] = {}
    expected: list[dict[str, Any]] = []
    for local_round in range(rounds + 1):
        absolute_round = round_base + local_round
        source_round = source_base + local_round
        for prefix in ("x", "y", "l", "z"):
            if prefix != "x" and local_round == rounds:
                continue
            mask = _mask_from_truncated(
                activity,
                section,
                prefix,
                source_round,
            )
            masks[f"{prefix}_{local_round}"] = mask
            expected.append(
                {
                    "name": f"{prefix}_{local_round}",
                    "mask": mask,
                    "absolute_round": absolute_round,
                }
            )
    return masks, expected


def _build_diff_for_truncated_side(
    truncated: TruncatedBoomerang,
    snapshot: dict[str, Any],
    params: dict[str, Any],
    side: str,
    deadline: float | None,
) -> tuple[Diff | None, list[dict[str, Any]], dict[str, str]]:
    if side == "upper":
        rounds = int(params["r0"])
        round_base = 0
        mode = 0
    else:
        rounds = int(params["r1"])
        round_base = int(params["r0"]) + int(params["rm"])
        mode = 1

    if rounds == 0:
        return None, [], {}

    masks, expected = _truncated_masks(
        snapshot["activity"],
        side,
        rounds,
        round_base,
        0 if side == "upper" else int(params["rm"]),
    )
    diff_params = {
        "nrounds": rounds,
        "mode": mode,
        "startweight": 0,
        "endweight": 128,
        "timelimit": _effective_time_limit(params, deadline),
        "fixedVariables": {},
        "truncatedMasks": masks,
        "round_offset": round_base,
    }
    diff = Diff(diff_params)
    diff.make_model()
    diff.load_model()
    return diff, expected, masks


def _check_support(
    characteristic: dict[str, Any],
    expected: list[dict[str, Any]],
    side: str,
    truncated_id: int,
    candidate_id: int,
) -> None:
    for item in expected:
        actual_value = characteristic.get(item["name"])
        if actual_value is None:
            raise SupportConsistencyError(
                f"{side} T{truncated_id} C{candidate_id}: missing {item['name']}"
            )
        actual = _support(actual_value)
        if actual != item["mask"]:
            raise SupportConsistencyError(
                f"{side} T{truncated_id} C{candidate_id}: support mismatch for "
                f"{item['name']} (absolute round {item['absolute_round']}), "
                f"expected={item['mask']}, actual={actual}"
            )


def _candidate_payload(
    extracted: dict[str, Any],
    smt_result: dict[str, Any],
    *,
    truncated_id: int,
    candidate_id: int,
    side: str,
    no_good: str | None = None,
) -> dict[str, Any]:
    return {
        "truncated_id": truncated_id,
        "candidate_id": candidate_id,
        "side": side,
        "weight": extracted["weight"],
        "round_offset": extracted["trail"]["round_offset"],
        "milp_status": "OPTIMAL",
        "smt_status": smt_result.get("status"),
        "verification_time_seconds": smt_result.get("elapsed_seconds"),
        "reason": smt_result.get("reason"),
        "nogood_constraint": no_good,
        "fixed_diffs": extracted["trail"]["fixed_diffs"],
        "trail": extracted["trail"],
        "characteristic": extracted["characteristic"],
        "witness": smt_result.get("witness"),
        "concrete_replay": (
            smt_result.get("witness", {}).get("concrete_replay")
            if isinstance(smt_result.get("witness"), dict)
            else None
        ),
    }


def _save_candidate(
    path_dir: Path,
    bucket: str,
    payload: dict[str, Any],
) -> None:
    directory = path_dir / payload["side"] / bucket
    directory.mkdir(parents=True, exist_ok=True)
    name = f"candidate_{payload['candidate_id']:04d}"
    _write_json(directory / f"{name}.json", payload)
    conclusion = {
        "UNSAT": "该完整 concrete differential characteristic 没有真实 witness。",
        "UNKNOWN": "Z3 未给出可判定结果；该 candidate 没有被 no-good 排除。",
        "SAT": "Z3 找到 witness，且 Python concrete replay 通过。",
    }.get(payload["smt_status"], "未知状态。")
    text = _trail_markdown(
        f"{payload['side'].capitalize()} Exact Candidate {payload['candidate_id']:04d}",
        payload["characteristic"],
    )
    text += (
        f"\n- Truncated path ID: {payload['truncated_id']}\n"
        f"- SMT status: {payload['smt_status']}\n"
        f"- Replay: {payload.get('concrete_replay', 'none')}\n"
        f"- Conclusion: {conclusion}\n"
    )
    (directory / f"{name}.md").write_text(text, encoding="utf-8")


def _save_accepted(path_dir: Path, payload: dict[str, Any]) -> None:
    directory = path_dir / payload["side"]
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / "accepted.json", payload)
    text = _trail_markdown(
        f"{payload['side'].capitalize()} Exact Accepted Candidate",
        payload["characteristic"],
    )
    witness = payload.get("witness", {})
    text += (
        f"\n- Truncated path ID: {payload['truncated_id']}\n"
        f"- Candidate ID: {payload['candidate_id']}\n"
        f"- SMT status: SAT\n"
        f"- Concrete replay: {payload.get('concrete_replay')}\n"
    )
    if witness:
        text += "\n## Real Witness\n\n"
        for key, value in witness.items():
            if isinstance(value, (str, int, float)):
                text += f"- {key}: {value}\n"
    (directory / "accepted.md").write_text(text, encoding="utf-8")


def _save_path_summary(path_dir: Path, summary: dict[str, Any]) -> None:
    _write_json(path_dir / "summary.json", summary)


def _refine_diff_until_exact_sat(
    diff: Diff | None,
    expected: list[dict[str, Any]],
    initial: dict[str, Any] | None,
    *,
    side: str,
    truncated_id: int,
    path_dir: Path,
    params: dict[str, Any],
    deadline: float | None,
    path_deadline: float | None,
) -> dict[str, Any]:
    if diff is None:
        print(
            f"\n{side.capitalize()} instantiated differential trail: "
            "0-round side; no concrete instantiation required"
        )
        return {
            "result": "SAT",
            "candidate_count": 0,
            "rejected_candidates": 0,
            "unknown_candidates": 0,
            "accepted": None,
        }

    status = initial or {"status": diff.last_solve_status}
    candidate_id = 0
    rejected = 0
    unknown = 0
    initial_weight = None
    last_weight = None

    while _has_milp_solution(status):
        if _remaining_seconds(deadline) == 0 or _remaining_seconds(path_deadline) == 0:
            return {
                "result": "TIMEOUT_SKIPPED",
                "reason": "PATH_TIMEOUT",
                "candidate_count": candidate_id,
                "rejected_candidates": rejected,
                "unknown_candidates": unknown,
                "initial_concrete_weight": initial_weight,
                "last_concrete_weight": last_weight,
            }
        candidate_id += 1
        extracted = diff.extract_solution_for_exact_verify()
        weight = extracted["weight"]
        initial_weight = weight if initial_weight is None else initial_weight
        last_weight = weight
        _check_support(
            extracted["characteristic"], expected, side, truncated_id, candidate_id
        )
        print(f"\n{side.capitalize()} exact candidate #{candidate_id}")
        print(f"MILP weight : {weight}")
        smt = verify_trail(
            extracted["trail"],
            timeout_ms=int(params["exact_verify_timeout_ms"]),
        )
        print(f"SMT status  : {smt['status']}")
        if smt["status"] == "SAT":
            replay = smt.get("witness", {}).get("concrete_replay")
            print(f"Replay      : {replay}")
            if replay != "PASS":
                payload = _candidate_payload(
                    extracted, smt, truncated_id=truncated_id,
                    candidate_id=candidate_id, side=side
                )
                _save_candidate(path_dir, "unknown", payload)
                return {
                    "result": "MODEL_INCONSISTENCY",
                    "candidate_count": candidate_id,
                    "rejected_candidates": rejected,
                    "unknown_candidates": unknown,
                    "accepted": None,
                    "model_inconsistency": payload,
                }
            print(f"\nAccepted {side} instantiated differential trail:\n")
            Diff.print_trail(extracted["characteristic"])
            payload = _candidate_payload(
                extracted, smt, truncated_id=truncated_id,
                candidate_id=candidate_id, side=side
            )
            _save_accepted(path_dir, payload)
            return {
                "result": "SAT",
                "candidate_count": candidate_id,
                "rejected_candidates": rejected,
                "unknown_candidates": unknown,
                "initial_concrete_weight": initial_weight,
                "last_concrete_weight": last_weight,
                "accepted": payload,
            }
        if smt["status"] == "UNKNOWN":
            unknown += 1
            payload = _candidate_payload(
                extracted, smt, truncated_id=truncated_id,
                candidate_id=candidate_id, side=side
            )
            _save_candidate(path_dir, "unknown", payload)
            print("Action      : keep candidate unblocked; skip this truncated path")
            return {
                "result": "TIMEOUT_SKIPPED",
                "reason": smt.get("reason", "Z3_UNKNOWN"),
                "candidate_count": candidate_id,
                "rejected_candidates": rejected,
                "unknown_candidates": unknown,
                "initial_concrete_weight": initial_weight,
                "last_concrete_weight": last_weight,
            }

        no_good = (
            f"exact_nogood_{side}_t{truncated_id:03d}_c{candidate_id:04d}"
        )
        payload = _candidate_payload(
            extracted, smt, truncated_id=truncated_id,
            candidate_id=candidate_id, side=side, no_good=no_good
        )
        _save_candidate(path_dir, "rejected", payload)
        diff.add_solution_nogood(extracted, no_good)
        rejected += 1
        print("Action      : block full concrete trail and re-optimize")
        status = diff.optimize_next_candidate(
            time_limit_override=_effective_time_limit(params, deadline)
        )

    if status.get("status") == "INFEASIBLE":
        return {
            "result": "CONCRETE_EXHAUSTED",
            "candidate_count": candidate_id,
            "rejected_candidates": rejected,
            "unknown_candidates": unknown,
            "initial_concrete_weight": initial_weight,
            "last_concrete_weight": last_weight,
        }
    return {
        "result": "TIMEOUT_SKIPPED",
        "reason": status.get("status", "MILP_STOPPED"),
        "candidate_count": candidate_id,
        "rejected_candidates": rejected,
        "unknown_candidates": unknown,
        "initial_concrete_weight": initial_weight,
        "last_concrete_weight": last_weight,
    }


def _prepare_truncated_model(params: dict[str, Any]) -> TruncatedBoomerang:
    bm = TruncatedBoomerang(
        params["r0"],
        params["rm"],
        params["r1"],
        params["w0"],
        params["wm"],
        params["w1"],
        time_limit=params["timelimit"],
    )
    bm.make_model()
    bm.load_model()
    return bm


def _run_exact_two_level_search(
    params: dict[str, Any],
    result_dir: Path,
    deadline: float | None,
    lp_cleaner: _InstantiationLpCleaner | None = None,
) -> dict[str, Any]:
    print("=" * 60)
    print("Exact verification enabled: two-level truncated/concrete search")
    print("=" * 60)
    bm = _prepare_truncated_model(params)
    raw_id = 0
    truncated_id = 0
    timeout_paths = 0
    truncated_search_stopped_incomplete = False
    paths: list[dict[str, Any]] = []
    lp_cleaner = lp_cleaner or _InstantiationLpCleaner()

    def finish_result(payload: dict[str, Any]) -> dict[str, Any]:
        cleanup = lp_cleaner.snapshot()
        payload["instantiation_lp_cleanup"] = cleanup
        summary = payload.get("summary")
        if isinstance(summary, dict):
            summary["instantiation_lp_cleanup"] = cleanup
            _write_json(result_dir / "exact_summary.json", summary)
        print(
            "Instantiation LP cleanup: "
            f"registered={cleanup['registered_files']} "
            f"deleted={cleanup['deleted_files']} "
            f"deleted_bytes={cleanup['deleted_bytes']} "
            f"retained={cleanup['retained_registered_files']} "
            f"refused={cleanup['refused_files']} "
            f"failures={cleanup['delete_failures']}"
        )
        return payload

    while True:
        if _remaining_seconds(deadline) == 0:
            break
        raw_status = bm.optimize_next_truncated_path(
            time_limit_override=_effective_time_limit(params, deadline)
        )
        if not _has_milp_solution(raw_status):
            truncated_search_stopped_incomplete = (
                raw_status.get("status") != "INFEASIBLE"
            )
            break
        raw_id += 1
        snapshot = bm.extract_current_truncated_path()
        upper, upper_expected, _ = _build_diff_for_truncated_side(
            bm, snapshot, params, "upper", deadline
        )
        lower, lower_expected, _ = _build_diff_for_truncated_side(
            bm, snapshot, params, "lower", deadline
        )
        lp_cleaner.register(upper)
        lp_cleaner.register(lower)
        upper_first = (
            {"status": "OPTIMAL"}
            if upper is None
            else upper.optimize_next_candidate(
                time_limit_override=_effective_time_limit(params, deadline)
            )
        )
        lower_first = (
            {"status": "OPTIMAL"}
            if lower is None
            else lower.optimize_next_candidate(
                time_limit_override=_effective_time_limit(params, deadline)
            )
        )
        if (
            upper_first.get("status") == "INFEASIBLE"
            or lower_first.get("status") == "INFEASIBLE"
        ):
            name = f"raw_truncated_nogood_{raw_id:04d}"
            print(
                f"Raw truncated candidate #{raw_id}: "
                f"upper={upper_first.get('status')}, lower={lower_first.get('status')}; "
                f"not counted in results, add {name}"
            )
            bm.add_truncated_path_nogood(snapshot, name)
            cleanup = lp_cleaner.delete_many(
                tuple(diff for diff in (upper, lower) if diff is not None),
                reason=f"raw_truncated_candidate_{raw_id}_rejected",
            )
            print(
                "Instantiation LP cleanup for raw rejection: "
                f"deleted={cleanup['deleted_files']} "
                f"deleted_bytes={cleanup['deleted_bytes']}"
            )
            continue
        if not _has_milp_solution(upper_first) or not _has_milp_solution(lower_first):
            global_summary = {
                "exact_verify_enabled": True,
                "result": "SEARCH_INCOMPLETE_TIMEOUT",
                "reason": "INITIAL_CONCRETE_MILP_STOPPED",
                "truncated_paths_tested": truncated_id,
                "truncated_paths": paths,
            }
            _write_json(result_dir / "exact_summary.json", global_summary)
            return finish_result({
                "result": "SEARCH_INCOMPLETE_TIMEOUT",
                "summary": global_summary,
            })

        truncated_id += 1
        path_dir = result_dir / f"truncated_{truncated_id:04d}"
        path_dir.mkdir(parents=True, exist_ok=True)
        _write_json(path_dir / "truncated_path.json", snapshot)
        (path_dir / "truncated_path.md").write_text(
            _truncated_markdown(snapshot), encoding="utf-8"
        )
        print(
            f"\nQualified truncated path #{truncated_id} "
            f"(raw candidate #{raw_id})"
        )
        print(f"Truncated MILP status : {raw_status.get('status', 'UNKNOWN')}")
        if raw_status.get("objective") is not None:
            print(f"Truncated objective   : {raw_status['objective']}")
        if raw_status.get("elapsed_seconds") is not None:
            print(
                "Truncated MILP time    : "
                f"{float(raw_status['elapsed_seconds']):.6f} seconds"
            )
        bm.print_truncated_solution(snapshot["activity"])
        path_started = time.monotonic()
        path_deadline = (
            path_started + params["exact_path_timeout_sec"]
            if params["exact_path_timeout_sec"] is not None
            else None
        )
        upper_result = _refine_diff_until_exact_sat(
            upper, upper_expected, upper_first, side="upper",
            truncated_id=truncated_id, path_dir=path_dir, params=params,
            deadline=deadline, path_deadline=path_deadline
        )
        lower_result: dict[str, Any] | None = None
        if upper_result["result"] == "SAT":
            lower_result = _refine_diff_until_exact_sat(
                lower, lower_expected, lower_first, side="lower",
                truncated_id=truncated_id, path_dir=path_dir, params=params,
                deadline=deadline, path_deadline=path_deadline
            )

        if (
            upper_result["result"] == "SAT"
            and lower_result is not None
            and lower_result["result"] == "SAT"
        ):
            summary = {
                "truncated_id": truncated_id,
                "raw_truncated_id": raw_id,
                "result": "SAT",
                "elapsed_seconds": time.monotonic() - path_started,
                "upper": upper_result,
                "lower": lower_result,
            }
            _save_path_summary(path_dir, summary)
            paths.append(summary)
            global_summary = {
                "exact_verify_enabled": True,
                "result": "SAT",
                "accepted_truncated_id": truncated_id,
                "accepted_weight_upper": (
                    upper_result["accepted"]["weight"]
                    if upper_result["accepted"] is not None
                    else 0.0
                ),
                "accepted_weight_lower": (
                    lower_result["accepted"]["weight"]
                    if lower_result["accepted"] is not None
                    else 0.0
                ),
                "truncated_paths_tested": truncated_id,
                "truncated_paths": paths,
            }
            _write_json(result_dir / "exact_summary.json", global_summary)
            return finish_result({
                "result": "SAT",
                "upper": (
                    upper_result["accepted"]["characteristic"]
                    if upper_result["accepted"] is not None
                    else None
                ),
                "lower": (
                    lower_result["accepted"]["characteristic"]
                    if lower_result["accepted"] is not None
                    else None
                ),
                "snapshot": snapshot,
                "summary": global_summary,
            })

        results = [upper_result]
        if lower_result is not None:
            results.append(lower_result)
        path_result = (
            "TIMEOUT_SKIPPED"
            if any(item["result"] == "TIMEOUT_SKIPPED" for item in results)
            else "CONCRETE_EXHAUSTED"
        )
        if any(item["result"] == "MODEL_INCONSISTENCY" for item in results):
            path_result = "MODEL_INCONSISTENCY"
        reason = next(
            (item.get("reason") for item in results if item.get("reason")),
            None,
        )
        summary = {
            "truncated_id": truncated_id,
            "raw_truncated_id": raw_id,
            "result": path_result,
            "reason": reason,
            "elapsed_seconds": time.monotonic() - path_started,
            "upper": upper_result,
            "lower": lower_result,
        }
        _save_path_summary(path_dir, summary)
        paths.append(summary)
        if path_result == "MODEL_INCONSISTENCY":
            global_summary = {
                "exact_verify_enabled": True,
                "result": "MODEL_INCONSISTENCY",
                "truncated_paths_tested": truncated_id,
                "truncated_paths": paths,
            }
            _write_json(result_dir / "exact_summary.json", global_summary)
            return finish_result({
                "result": "MODEL_INCONSISTENCY",
                "summary": global_summary,
            })
        if path_result == "TIMEOUT_SKIPPED":
            timeout_paths += 1
        no_good = f"truncated_nogood_{truncated_id:04d}"
        bm.add_truncated_path_nogood(snapshot, no_good)
        summary["truncated_nogood"] = no_good
        _save_path_summary(path_dir, summary)
        cleanup_targets: list[Any] = []
        if upper_result["result"] == "CONCRETE_EXHAUSTED":
            if upper is not None:
                cleanup_targets.append(upper)
            if lower_result is None and lower is not None:
                cleanup_targets.append(lower)
        if (
            lower_result is not None
            and lower_result["result"] == "CONCRETE_EXHAUSTED"
            and lower is not None
        ):
            cleanup_targets.append(lower)
        cleanup = lp_cleaner.delete_many(
            cleanup_targets,
            reason=f"truncated_candidate_{truncated_id}_{path_result}",
        )
        print(
            f"Instantiation LP cleanup for truncated #{truncated_id}: "
            f"deleted={cleanup['deleted_files']} "
            f"deleted_bytes={cleanup['deleted_bytes']}"
        )

    result = (
        "SEARCH_INCOMPLETE_TIMEOUT"
        if (
            timeout_paths
            or truncated_search_stopped_incomplete
            or _remaining_seconds(deadline) == 0
        )
        else "NO_EXACT_REALIZABLE_TRAIL"
    )
    global_summary = {
        "exact_verify_enabled": True,
        "result": result,
        "truncated_paths_tested": truncated_id,
        "timeout_skipped_paths": timeout_paths,
        "truncated_paths": paths,
    }
    _write_json(result_dir / "exact_summary.json", global_summary)
    return finish_result({"result": result, "summary": global_summary})


def _run_legacy_search(
    params: dict[str, Any],
    deadline: float | None,
) -> dict[str, Any]:
    timing = {
        "truncated_search_seconds": None,
        "upper_instantiation_seconds": None,
        "lower_instantiation_seconds": None,
    }
    bm = _prepare_truncated_model(params)
    truncated_started = time.monotonic()
    status = bm.find_truncated_boomerang_trail(
        time_limit_override=_effective_time_limit(params, deadline)
    )
    timing["truncated_search_seconds"] = time.monotonic() - truncated_started
    print(
        "Truncated trail search time: "
        f"{timing['truncated_search_seconds']:.6f} seconds"
    )
    if not _has_milp_solution(status):
        return {
            "result": status.get("status", "INFEASIBLE"),
            "timing": timing,
        }
    bm.parse_solver_output()
    snapshot = bm.extract_current_truncated_path()

    upper_started = time.monotonic()
    upper, _, _ = _build_diff_for_truncated_side(
        bm, snapshot, params, "upper", deadline
    )
    upper_trail = None
    if upper is not None:
        upper_trail = upper.find_characteristic(
            time_limit_override=_effective_time_limit(params, deadline)
        )
    timing["upper_instantiation_seconds"] = time.monotonic() - upper_started
    print(
        "Upper Diff instantiation time: "
        f"{timing['upper_instantiation_seconds']:.6f} seconds"
    )
    if upper is not None and upper_trail is None:
        return {
            "result": upper.last_solve_status or "UPPER_DIFF_STOPPED",
            "upper_status": upper.last_solve_status,
            "timing": timing,
        }

    lower_started = time.monotonic()
    lower, _, _ = _build_diff_for_truncated_side(
        bm, snapshot, params, "lower", deadline
    )
    lower_trail = None
    if lower is not None:
        lower_trail = lower.find_characteristic(
            time_limit_override=_effective_time_limit(params, deadline)
        )
    timing["lower_instantiation_seconds"] = time.monotonic() - lower_started
    print(
        "Lower Diff instantiation time: "
        f"{timing['lower_instantiation_seconds']:.6f} seconds"
    )
    if lower is not None and lower_trail is None:
        return {
            "result": lower.last_solve_status or "LOWER_DIFF_STOPPED",
            "lower_status": lower.last_solve_status,
            "timing": timing,
        }
    return {
        "result": "SAT",
        "upper": upper_trail,
        "lower": lower_trail,
        "snapshot": snapshot,
        "timing": timing,
    }


def _save_formal_results(
    result_dir: Path,
    params: dict[str, Any],
    upper: dict[str, Any] | None,
    lower: dict[str, Any] | None,
    exact_summary: dict[str, Any] | None,
    truncated_snapshot: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if (
        (upper is None and int(params["r0"]) != 0)
        or (lower is None and int(params["r1"]) != 0)
    ):
        return None
    snapshot = exact_summary.get("truncated_paths", []) if exact_summary else []
    path_snapshot = None
    if snapshot:
        accepted = exact_summary.get("accepted_truncated_id")
        path_snapshot = next(
            (item for item in snapshot if item.get("truncated_id") == accepted),
            None,
        )
    if truncated_snapshot is not None:
        activity = truncated_snapshot["activity"]
    elif path_snapshot is not None:
        path_file = result_dir / f"truncated_{path_snapshot['truncated_id']:04d}" / "truncated_path.json"
        truncated = json.loads(path_file.read_text(encoding="utf-8"))
        activity = truncated["activity"]
    else:
        activity = {"upper_trail": {}, "middle_part": {}, "lower_trail": {}}
    result = {
        "parameters": dict(params),
        "upper_trail": activity.get("upper_trail", {}),
        "middle_part": activity.get("middle_part", {}),
        "lower_trail": activity.get("lower_trail", {}),
        "diff_upper_trail": upper,
        "diff_lower_trail": lower,
        "diff_effect_upper_log2": -float(upper["total_weight"]) if upper else 0.0,
        "diff_effect_lower_log2": -float(lower["total_weight"]) if lower else 0.0,
        "exact_summary": exact_summary,
    }
    case_name = result_case_dir_name(params)
    _write_json(result_dir / f"{case_name}.json", result)
    _write_json(result_dir / "upper_diff_trail.json", upper)
    _write_json(result_dir / "lower_diff_trail.json", lower)
    _write_json(
        result_dir / "rounds.json",
        {
            "r0": params["r0"],
            "rm": params["rm"],
            "r1": params["r1"],
            "upper": upper,
            "lower": lower,
            "exact_summary": exact_summary,
        },
    )
    upper_text = (
        _trail_markdown("Upper differential trail", upper)
        if upper is not None
        else "# Upper differential trail\n\n0-round side; no concrete instantiation required.\n"
    )
    lower_text = (
        _trail_markdown("Lower differential trail", lower)
        if lower is not None
        else "# Lower differential trail\n\n0-round side; no concrete instantiation required.\n"
    )
    (result_dir / "upper_diff_trail.txt").write_text(upper_text, encoding="utf-8")
    (result_dir / "lower_diff_trail.txt").write_text(lower_text, encoding="utf-8")
    (result_dir / f"{case_name}.txt").write_text(
        _truncated_markdown({"activity": activity, "weight": None}),
        encoding="utf-8",
    )
    save_markdown_distinguisher(
        result_dir / f"{case_name}.json",
        result_dir / f"{case_name}_distinguisher.md",
    )
    (result_dir / "bmd.tex").write_text(
        tex_init()
        + tex_middle(
            result["upper_trail"],
            result["middle_part"],
            result["lower_trail"],
            params["r0"],
            params["rm"],
            params["r1"],
        )
        + tex_fin(params["r0"] + params["rm"] + params["r1"]),
        encoding="utf-8",
    )
    return result


def _print_search_summary(
    params: dict[str, Any],
    upper: dict[str, Any],
    lower: dict[str, Any],
    snapshot: dict[str, Any] | None,
) -> None:
    middle = (snapshot or {}).get("activity", {}).get("middle_part", {})
    common = int(middle.get("as", 0))
    upper_log2 = -float(upper["total_weight"]) if upper is not None else 0.0
    lower_log2 = -float(lower["total_weight"]) if lower is not None else 0.0
    print("\n" + "=" * 60)
    print(
        f"Boomerang distinguisher for "
        f"{params['r0']} + {params['rm']} + {params['r1']} "
    )
    print("#" * 60)
    print("Summary of the results:")
    print("Upper trail:")
    if upper is None:
        print("0-round side; no concrete instantiation required")
    else:
        Diff.print_trail(upper)
    print("#" * 60)
    print(f"Sandwich {params['rm']} rounds in the middle with {common} active S-boxes")
    print("#" * 60)
    print("Lower trail:")
    if lower is None:
        print("0-round side; no concrete instantiation required")
    else:
        Diff.print_trail(lower)
    print("-" * 27)
    print(f"differential effect of the upper trail: 2^({upper_log2:.2f})")
    print(f"differential effect of the lower trail: 2^({lower_log2:.2f})")
    print(
        "Total probability = p^2*q^2*r = "
        f"2^({2 * upper_log2:.2f}) x 2^({2 * lower_log2:.2f}) x r"
    )
    print(
        f"2^({2 * upper_log2 + 2 * lower_log2 - 2.5 * common:.2f}) <= "
        f"Total probability <= "
        f"2^({2 * upper_log2 + 2 * lower_log2 - 2 * common:.2f})"
    )
    print(
        "To compute the accurate value of total probability, r should be "
        "evaluated experimentally or using the (F)BCT framework"
    )


def _run_search(params: dict[str, Any], result_dir: Path) -> dict[str, Any]:
    started = time.monotonic()
    deadline = (
        started + params["global_tl"] if params["global_tl"] is not None else None
    )
    print("=" * 72)
    print("Search time limits")
    print(f"single MILP optimize : {params['timelimit']:g} seconds")
    print(
        "global search       : "
        + (
            f"{params['global_tl']} seconds"
            if params["global_tl"] is not None
            else "unlimited"
        )
    )
    print("probability test     : excluded from global search time")
    if params["exact_verify"]:
        output = _run_exact_two_level_search(params, result_dir, deadline)
    else:
        output = _run_legacy_search(params, deadline)

    if output.get("result") == "SAT":
        formal_result = _save_formal_results(
            result_dir,
            params,
            output.get("upper"),
            output.get("lower"),
            output.get("summary"),
            output.get("snapshot"),
        )
        _print_search_summary(
            params,
            output["upper"],
            output["lower"],
            output.get("snapshot"),
        )
        if params["probtest"] == "t" and formal_result is not None:
            probability_result, probability_log = save_probability_checks(
                formal_result, result_dir
            )
            print(probability_log, end="")
            _write_json(result_dir / "probability_summary.json", probability_result)
    else:
        print(f"\nSearch result: {output.get('result')}")
    print(f"Total solving time: {time.monotonic() - started:.6f} seconds")
    return output


def loadparameters(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "r0": args.r0,
        "rm": args.rm,
        "r1": args.r1,
        "w0": args.w0,
        "wm": args.wm,
        "w1": args.w1,
        "timelimit": args.timelimit,
        "global_tl": args.global_tl,
        "exact_verify": args.exact_verify,
        "exact_verify_timeout_ms": args.exact_verify_timeout_ms,
        "exact_path_timeout_sec": args.exact_path_timeout_sec,
        "probtest": args.probtest,
    }


def make_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-r0", type=int, default=2)
    parser.add_argument("-rm", type=int, default=3)
    parser.add_argument("-r1", type=int, default=2)
    parser.add_argument("-w0", type=int, default=6)
    parser.add_argument("-wm", type=int, default=3)
    parser.add_argument("-w1", type=int, default=6)
    parser.add_argument("-tl", "--timelimit", type=float, default=60.0)
    parser.add_argument(
        "--global-tl",
        type=float,
        default=None,
        help="whole search deadline in seconds; probability testing is excluded",
    )
    parser.add_argument("--exact-verify", action="store_true")
    parser.add_argument("--exact-verify-timeout-ms", type=int, default=300000)
    parser.add_argument("--exact-path-timeout-sec", type=float, default=None)
    parser.add_argument("--probtest", choices=("t", "f"), default="f")
    return parser


def main_for_result_root(
    argv: list[str] | None = None,
    result_root_name: str = "results",
) -> int:
    os.chdir(PROJECT_DIR)
    args = make_argument_parser().parse_args(argv)
    params = loadparameters(args)
    result_dir = PROJECT_DIR / result_root_name / result_case_dir_name(params)
    with _tee_terminal_output(result_dir / "terminal_print.txt"):
        started = time.monotonic()
        try:
            output = _run_search(params, result_dir)
            print(f"Results saved in {result_dir.resolve()}")
        except BaseException:
            traceback.print_exc()
            print(
                "Total solving time before failure: "
                f"{time.monotonic() - started:.6f} seconds"
            )
            return 1
    return 0 if output.get("result") == "SAT" else 1


def main(argv: list[str] | None = None) -> int:
    return main_for_result_root(argv, "results")


if __name__ == "__main__":
    raise SystemExit(main())
