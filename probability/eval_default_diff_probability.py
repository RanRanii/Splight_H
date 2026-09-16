"""Evaluate default Splight-H upper/lower differential probabilities.

This helper reads ``results/result.json`` produced by ``boom.py`` and reuses
``Splight_implement/implement_py/probability_tests/probability_test.py`` to
estimate the concrete input-output differential probabilities for the upper
and lower trails.
"""

import argparse
import json
import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PROB_TEST_DIR = ROOT / "Splight_implement" / "implement_py" / "probability_tests"
sys.path.insert(0, str(PROB_TEST_DIR))

from probability_test import run_experiment  # noqa: E402


def _trail_io(trail):
    rounds = int(trail["nrounds"])
    return trail["x_0"], trail[f"x_{rounds}"], rounds


def evaluate(result_file, batch_size="2^10", trials=1, seed=20260625):
    data = json.loads(Path(result_file).read_text(encoding="utf-8"))
    jobs = {
        "upper": _trail_io(data["diff_upper_trail"]),
        "lower": _trail_io(data["diff_lower_trail"]),
    }
    results = {}
    for name, (input_diff, output_diff, rounds) in jobs.items():
        print("=" * 72)
        print(f"{name} differential probability experiment")
        result = run_experiment(
            overrides={
                "mode": "diff",
                "rounds": rounds,
                "input_diff": input_diff,
                "output_diff": output_diff,
                "trials": trials,
                "batch_size": batch_size,
                "seed": seed,
            }
        )
        result["log2_probability"] = None if result["right"] == 0 else math.log2(result["probability"])
        results[name] = result
    return results


def main():
    parser = argparse.ArgumentParser(description="Evaluate default Splight-H upper/lower differential probabilities.")
    parser.add_argument("--result-file", default="results/result.json")
    parser.add_argument("--batch-size", default="2^10")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260625)
    parser.add_argument("--output-json", default="results/diff_probability_2_10.json")
    parser.add_argument("--output-txt", default="results/diff_probability_2_10.txt")
    args = parser.parse_args()

    results = evaluate(args.result_file, args.batch_size, args.trials, args.seed)
    Path(args.output_json).write_text(json.dumps(results, indent=2), encoding="utf-8")

    lines = []
    for name, result in results.items():
        lines.append(f"{name}:")
        lines.append(f"  rounds: {result['rounds']}")
        lines.append(f"  input_diff: {result['input_diff']}")
        lines.append(f"  output_diff: {result['output_diff']}")
        lines.append(f"  right/total: {result['right']}/{result['total']}")
        lines.append(f"  probability: {result['probability']}")
        lines.append(f"  log2_probability: {result['log2_probability']}")
    Path(args.output_txt).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
