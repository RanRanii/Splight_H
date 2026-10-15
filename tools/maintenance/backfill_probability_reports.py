#!/usr/bin/env python3

"""Backfill Splight-H probability Markdown reports and rename legacy logs."""

import json
from pathlib import Path
import sys


BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

from run.probtest_from_results import (  # noqa: E402
    probability_report_stem,
    write_probability_markdown,
)


PROBABILITY_RESULTS_DIR = BASE_DIR / "probability_results"


def main():
    reports_written = 0
    logs_renamed = 0
    missing_logs = 0

    for output_dir in sorted(path for path in PROBABILITY_RESULTS_DIR.iterdir() if path.is_dir()):
        summary_path = output_dir / "summary.json"
        if not summary_path.is_file():
            print(f"skip missing summary: {output_dir.name}")
            continue

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        stem = probability_report_stem(summary, output_dir.name)
        legacy_log = output_dir / "probability_tests.txt"
        target_log = output_dir / f"{stem}_probtest.txt"

        if legacy_log.is_file():
            if target_log.exists():
                raise FileExistsError(
                    f"refusing to overwrite existing probability log: {target_log}"
                )
            legacy_log.rename(target_log)
            logs_renamed += 1
        elif not target_log.is_file():
            missing_logs += 1

        report_path = write_probability_markdown(output_dir, summary)
        reports_written += 1
        print(report_path.relative_to(BASE_DIR))

    print(f"Markdown reports written: {reports_written}")
    print(f"Probability logs renamed: {logs_renamed}")
    print(f"Directories without probability log: {missing_logs}")


if __name__ == "__main__":
    main()
