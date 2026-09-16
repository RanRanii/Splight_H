#!/usr/bin/env python3
"""Run the existing Splight-H probability evaluator from saved search results."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
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
    with tee_output(output_dir / "terminal_print.txt"):
        started = time.perf_counter()
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
            "status": "success",
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
        (output_dir / "probability_tests.txt").write_text(text, encoding="utf-8")
        print(f"Saved: {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
