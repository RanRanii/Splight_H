#!/usr/bin/env python3
"""Run the existing Splight-H probability evaluator from saved search results."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import time
import traceback
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from probability.probability_evaluator import (  # noqa: E402
    format_probability_log,
    load_probability_input_from_results,
    run_probability_checks,
)


class _Tee(io.TextIOBase):
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
def tee_output(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        with contextlib.redirect_stdout(_Tee(sys.stdout, handle)):
            with contextlib.redirect_stderr(_Tee(sys.stderr, handle)):
                yield


def _rounds_from_case_name(case_name):
    match = re.fullmatch(r"(\d+)-(\d+)-(\d+)(?:_\d+)?", str(case_name))
    if match is None:
        raise ValueError(
            "probability result directory name must be "
            "<r0>-<rm>-<r1> or <r0>-<rm>-<r1>_<w0><wm><w1>"
        )
    r0, rm, r1 = (int(value) for value in match.groups())
    return {"r0": r0, "rm": rm, "r1": r1}


def probability_report_stem(summary, case_name):
    rounds = _rounds_from_case_name(case_name)
    r0 = int(summary.get("r0", rounds["r0"]))
    rm = int(summary.get("rm", rounds["rm"]))
    r1 = int(summary.get("r1", rounds["r1"]))
    return f"{r0}-{rm}-{r1}"


def _display(value, digits=None):
    if value is None:
        return "n/a"
    if digits is not None:
        try:
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            pass
    return str(value)


def _experiment_status(entry):
    if not entry:
        return "UNAVAILABLE"
    if entry.get("skipped"):
        return "SKIPPED"
    if entry.get("probability") is None:
        return "UNAVAILABLE"
    return "SUCCESS"


def _right_total(entry, right_key="right", total_key="total"):
    if not entry:
        return "n/a"
    right = entry.get(right_key)
    total = entry.get(total_key)
    if right is None or total is None:
        return "n/a"
    return f"{right} / {total}"


def _middle_theory_log2(middle):
    if not middle:
        return "n/a"
    lower = middle.get("hadipour_lower_log2")
    upper = middle.get("hadipour_upper_log2")
    if lower is None or upper is None:
        return "n/a"
    if float(lower) == float(upper):
        return _display(lower, 6)
    return f"[{_display(lower, 6)}, {_display(upper, 6)}]"


def _error_message(error):
    lines = [line.strip() for line in str(error or "").splitlines() if line.strip()]
    return lines[-1] if lines else "Unknown error"


def render_probability_markdown(summary, case_name, probability_log_exists=True):
    stem = probability_report_stem(summary, case_name)
    status = str(summary.get("status", "UNKNOWN")).upper()
    lines = [
        f"# Splight-H Probability Summary: {stem}",
        "",
        "## Overall Result",
        "",
        f"- Status: `{status}`",
        f"- Result case: `{case_name}`",
    ]

    if status != "SUCCESS":
        lines.extend([
            f"- Elapsed time: `{_display(summary.get('elapsed_seconds'), 6)} seconds`",
            "",
            "## Error",
            "",
            f"`{_error_message(summary.get('error'))}`",
            "",
            "## Related Files",
            "",
            (
                f"- Full probability log: `{stem}_probtest.txt`"
                if probability_log_exists
                else "- Full probability log: not generated"
            ),
            "- Terminal output: `terminal_print.txt`",
            "- Summary JSON: `summary.json`",
            "",
        ])
        return "\n".join(lines)

    probability = summary.get("probability_test") or {}
    parameters = probability.get("parameters") or {}
    diff = probability.get("diff") or {}
    upper = (diff.get("upper") or {}).get("result") or {}
    lower = (diff.get("lower") or {}).get("result") or {}
    middle = probability.get("em_boomerang") or {}
    total = probability.get("total_boomerang_probability") or {}
    accepted_id = summary.get("accepted_truncated_id")
    accepted_path = (
        f"truncated_{int(accepted_id):04d}" if accepted_id is not None else "n/a"
    )

    lines.extend([
        f"- Trail source: `{_display(summary.get('trail_source'))}`",
        f"- Accepted truncated path: `{accepted_path}`",
        f"- Random seed: `{_display(parameters.get('seed'))}`",
        f"- Elapsed time: `{_display(summary.get('elapsed_seconds'), 6)} seconds`",
        "",
        "## Boomerang Probability",
        "",
        "| Component | Symbol | Theoretical log2 | Experimental log2 | Right / Total | Status |",
        "|---|---:|---:|---:|---:|---|",
        "| Upper differential | $p$ | `{}` | `{}` | `{}` | `{}` |".format(
            _display(upper.get("theory_log2"), 6),
            _display(upper.get("log2_probability"), 6),
            _right_total(upper),
            _experiment_status(upper),
        ),
        "| Middle switch | $r$ | `{}` | `{}` | `{}` | `{}` |".format(
            _middle_theory_log2(middle),
            _display(middle.get("log2_r"), 6),
            _right_total(middle, "returned_count", "trials"),
            _experiment_status({
                "skipped": middle.get("skipped"),
                "probability": middle.get("estimated_r"),
            }),
        ),
        "| Lower differential | $q$ | `{}` | `{}` | `{}` | `{}` |".format(
            _display(lower.get("theory_log2"), 6),
            _display(lower.get("log2_probability"), 6),
            _right_total(lower),
            _experiment_status(lower),
        ),
        "",
    ])

    total_log2 = total.get("log2_probability")
    if total.get("status") == "available" and total_log2 is not None:
        lines.append(
            "- Experimental estimate: "
            f"$\\hat p^2 \\times \\hat r \\times \\hat q^2 = "
            f"2^{{{float(total_log2):.6f}}}$"
        )
    else:
        lines.append(
            "- Experimental estimate: unavailable"
            + (f" (`{total.get('reason')}`)" if total.get("reason") else "")
        )
    theory_upper = upper.get("theory_log2")
    theory_lower = lower.get("theory_log2")
    middle_lower = middle.get("hadipour_lower_log2")
    middle_upper = middle.get("hadipour_upper_log2")
    if all(value is not None for value in (
        theory_upper, theory_lower, middle_lower, middle_upper
    )):
        base = 2.0 * float(theory_upper) + 2.0 * float(theory_lower)
        lower_bound = base + float(middle_lower)
        upper_bound = base + float(middle_upper)
        lines.append(
            f"- Theoretical bound: $2^{{{lower_bound:.6f}}} "
            f"\\leq p^2rq^2 \\leq 2^{{{upper_bound:.6f}}}$"
        )

    lines.extend([
        "",
        "## Upper Differential",
        "",
        f"- Rounds: `{_display(upper.get('rounds'))}`",
        f"- Input difference: `{_display(upper.get('input_diff'))}`",
        f"- Output difference: `{_display(upper.get('output_diff'))}`",
        f"- Data size: `{_display(upper.get('data_size'))}`",
        f"- Right pairs: `{_display(upper.get('right'))}`",
        f"- Probability: `{_display(upper.get('probability'))}`",
        f"- $\\log_2$ probability: `{_display(upper.get('log2_probability'), 6)}`",
        "",
        "## Middle Boomerang Switch",
        "",
        f"- Rounds: `{_display(middle.get('rm'))}`",
        f"- $\\Delta$: `{_display(middle.get('input_diff_delta'))}`",
        f"- $\\nabla$: `{_display(middle.get('output_diff_nabla'))}`",
        f"- Common active S-boxes: `{_display(middle.get('common_active_sboxes'))}`",
        f"- Data size: `{_display(middle.get('data_size'))}`",
        f"- Right quartets: `{_display(middle.get('returned_count'))}`",
        f"- Probability: `{_display(middle.get('estimated_r'))}`",
        f"- $\\log_2$ probability: `{_display(middle.get('log2_r'), 6)}`",
        "",
        "## Lower Differential",
        "",
        f"- Rounds: `{_display(lower.get('rounds'))}`",
        f"- Input difference: `{_display(lower.get('input_diff'))}`",
        f"- Output difference: `{_display(lower.get('output_diff'))}`",
        f"- Data size: `{_display(lower.get('data_size'))}`",
        f"- Right pairs: `{_display(lower.get('right'))}`",
        f"- Probability: `{_display(lower.get('probability'))}`",
        f"- $\\log_2$ probability: `{_display(lower.get('log2_probability'), 6)}`",
        "",
        "## Related Files",
        "",
        f"- Full probability log: `{stem}_probtest.txt`",
        "- Terminal output: `terminal_print.txt`",
        "- Summary JSON: `summary.json`",
        "",
    ])
    return "\n".join(lines)


def write_probability_markdown(output_dir, summary):
    output_dir = Path(output_dir)
    stem = probability_report_stem(summary, output_dir.name)
    path = output_dir / f"{stem}_prob.md"
    path.write_text(
        render_probability_markdown(
            summary,
            output_dir.name,
            probability_log_exists=(output_dir / f"{stem}_probtest.txt").is_file(),
        ),
        encoding="utf-8",
    )
    return path


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-dir", required=True, help="completed results/<case> directory")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="override the existing probability evaluator's date-derived seed",
    )
    return parser


def main(argv=None):
    args = make_parser().parse_args(argv)
    result_dir = Path(args.result_dir).resolve()
    output_dir = PROJECT_DIR / "probability_results" / result_dir.name
    case_rounds = _rounds_from_case_name(result_dir.name)
    report_stem = probability_report_stem(case_rounds, result_dir.name)
    probability_log_name = f"{report_stem}_probtest.txt"
    with tee_output(output_dir / "terminal_print.txt"):
        started = time.perf_counter()
        try:
            print("=" * 72)
            print("从已有结果执行单密钥概率测试")
            print(f"result dir : {result_dir}")
            result = load_probability_input_from_results(result_dir)
            print(f"trail source: {result['trail_source']}")
            if result["accepted_truncated_id"] is not None:
                print(f"accepted truncated id: {result['accepted_truncated_id']}")
            probability = run_probability_checks(result, seed=args.seed)
            text = format_probability_log(probability)
            print(text, end="")
            summary = {
                "status": "SUCCESS",
                "result_case": result_dir.name,
                "r0": result["parameters"]["r0"],
                "rm": result["parameters"]["rm"],
                "r1": result["parameters"]["r1"],
                "trail_source": result["trail_source"],
                "accepted_truncated_id": result["accepted_truncated_id"],
                "probability_test": probability,
                "elapsed_seconds": time.perf_counter() - started,
            }
            (output_dir / "summary.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
            (output_dir / probability_log_name).write_text(text, encoding="utf-8")
            legacy_log_path = output_dir / "probability_tests.txt"
            if legacy_log_path.is_file():
                legacy_log_path.unlink()
            markdown_path = write_probability_markdown(output_dir, summary)
            print(f"Saved: {output_dir / 'summary.json'}")
            print(f"Saved: {markdown_path}")
            return 0
        except Exception:
            failure_summary = {
                "status": "FAILURE",
                "result_case": result_dir.name,
                **case_rounds,
                "elapsed_seconds": time.perf_counter() - started,
                "error": traceback.format_exc(),
            }
            (output_dir / "summary.json").write_text(
                json.dumps(failure_summary, indent=2), encoding="utf-8"
            )
            write_probability_markdown(output_dir, failure_summary)
            traceback.print_exc()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
