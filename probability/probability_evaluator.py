"""Probability checks used by boom.py after a distinguisher is instantiated."""

import contextlib
import io
import json
import math
import sys
from datetime import datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
PROB_TEST_DIR = ROOT / "Splight_implement" / "implement_py" / "probability_tests"
sys.path.insert(0, str(PROB_TEST_DIR))

from probability.eval_em import estimate_em_probability  # noqa: E402
from probability_test import run_experiment  # noqa: E402


MAX_DATA_EXPONENT = 16
MIN_EM_DATA_EXPONENT = 12


def log2_or_none(value):
    return None if value <= 0 else math.log2(value)


def today_seed():
    return int(datetime.now().strftime("%Y%m%d"))


def data_exponent(weight):
    return int(math.ceil(float(weight))) + 2


def data_size_from_exponent(exponent):
    return 2 ** int(exponent)


def should_skip(exponent):
    return int(exponent) >= MAX_DATA_EXPONENT


def run_with_suppressed_stdout(func, *args, **kwargs):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        return func(*args, **kwargs)


def mask_to_concrete_hex(mask):
    return "".join("1" if ch != "0" else "0" for ch in mask)


def skipped_result(kind, exponent, reason):
    return {
        "kind": kind,
        "skipped": True,
        "data_size_exponent": int(exponent),
        "data_size": f"2^({int(exponent)})",
        "reason": reason,
    }


def evaluate_diff_probability(result, seed):
    experiments = {}
    for name, trail in (("upper", result["diff_upper_trail"]), ("lower", result["diff_lower_trail"])):
        if trail is None:
            experiments[name] = {"result": None}
            continue
        rounds = int(trail["nrounds"])
        theory_weight = float(trail["total_weight"])
        exponent = data_exponent(theory_weight)
        if should_skip(exponent):
            experiments[name] = {
                "result": skipped_result(
                    "diff",
                    exponent,
                    f"data size 2^({exponent}) >= 2^({MAX_DATA_EXPONENT}); skip test",
                )
            }
            continue
        total = data_size_from_exponent(exponent)
        exp_result = run_with_suppressed_stdout(
            run_experiment,
            overrides={
                "mode": "diff",
                "rounds": rounds,
                "input_diff": trail["x_0"],
                "output_diff": trail[f"x_{rounds}"],
                "trials": 1,
                "batch_size": total,
                "seed": seed,
            },
        )
        exp_result["skipped"] = False
        exp_result["data_size_exponent"] = exponent
        exp_result["data_size"] = f"2^({exponent})"
        exp_result["log2_probability"] = log2_or_none(exp_result["probability"])
        exp_result["theory_log2"] = -theory_weight
        exp_result["theory_probability"] = 2 ** exp_result["theory_log2"]
        exp_result["delta_log2_exp_minus_theory"] = (
            None
            if exp_result["log2_probability"] is None
            else exp_result["log2_probability"] - exp_result["theory_log2"]
        )
        experiments[name] = {"result": exp_result}
    return experiments


def evaluate_em_boomerang_probability(result, seed):
    params = result["parameters"]
    r0 = int(params["r0"])
    rm = int(params["rm"])
    common_active = int(result["middle_part"]["as"])
    if result.get("diff_upper_trail") is None:
        delta = mask_to_concrete_hex(result["upper_trail"][f"x_{r0}"])
        delta_source = f"upper_trail[x_{r0}] active mask mapped 1->0x1"
    else:
        delta = result["diff_upper_trail"][f"x_{r0}"]
        delta_source = f"diff_upper_trail[x_{r0}]"
    if result.get("diff_lower_trail") is None:
        nabla = mask_to_concrete_hex(result["lower_trail"]["x_0"])
        nabla_source = "lower_trail[x_0] active mask mapped 1->0x1"
    else:
        nabla = result["diff_lower_trail"]["x_0"]
        nabla_source = "diff_lower_trail[x_0]"
    em_weight = 2.0 * common_active
    exponent = max(MIN_EM_DATA_EXPONENT, data_exponent(em_weight))
    base = {
        "mode": "boomerang switch",
        "test_scope": "Em only",
        "input_diff_delta": delta,
        "output_diff_nabla": nabla,
        "delta_source": delta_source,
        "nabla_source": nabla_source,
        "rm": rm,
        "common_active_sboxes": common_active,
        "hadipour_upper_log2": -2.0 * common_active,
        "hadipour_lower_log2": -2.5 * common_active,
        "data_size_exponent": exponent,
        "data_size": f"2^({exponent})",
    }
    if should_skip(exponent):
        base.update({
            "skipped": True,
            "reason": f"data size 2^({exponent}) >= 2^({MAX_DATA_EXPONENT}); skip test",
        })
        return base
    trials = data_size_from_exponent(exponent)
    em = estimate_em_probability(delta, nabla, rm, trials, seed=seed)
    em.update(base)
    em["skipped"] = False
    if em["estimated_r"] > 0:
        em["delta_log2_vs_upper_bound"] = em["log2_r"] - em["hadipour_upper_log2"]
        em["delta_log2_vs_lower_bound"] = em["log2_r"] - em["hadipour_lower_log2"]
    else:
        em["delta_log2_vs_upper_bound"] = None
        em["delta_log2_vs_lower_bound"] = None
    return em


def format_log2(value):
    return f"{value:.6f}" if value is not None else "-inf (no right pairs/quartets observed)"


def format_diff_section(label, entry):
    result = entry["result"]
    lines = ["", "-" * 72, f"{label}差分概率测试"]
    if result is None:
        lines.append("未生成具体差分路径，跳过测试。")
        return lines
    if result.get("skipped"):
        lines.extend([
            "mode          : diff",
            f"data size     : {result['data_size']}",
            f"skip reason   : {result['reason']}",
        ])
        return lines
    lines.extend([
        f"mode          : {result['mode']}",
        f"rounds        : {result['rounds']}",
        f"input diff    : {result['input_diff']}",
        f"output diff   : {result['output_diff']} (L_r||R_r)",
        "trials        : 1",
        f"data size     : {result['data_size']}",
        (
            "trial 0001: right={right} total={total} probability={probability:.12g}".format(
                right=result["right"],
                total=result["data_size"],
                probability=result["probability"],
            )
        ),
        "-" * 72,
        f"aggregate right pairs/quartets : {result['right']}",
        f"aggregate total pairs/quartets : {result['data_size']}",
        f"estimated probability          : {result['probability']:.12g}",
        f"log2(probability)              : {format_log2(result['log2_probability'])}",
    ])
    return lines


def format_em_section(em):
    lines = ["", "-" * 72, "Em 中间 boomerang switch 概率测试"]
    lines.extend([
        f"mode          : {em['mode']}",
        f"scope         : {em['test_scope']}",
        f"rounds        : {em['rm']}",
        f"input diff    : {em['input_diff_delta']} (Delta from upper output)",
        f"output diff   : {em['output_diff_nabla']} (nabla from lower input)",
        f"common active : {em['common_active_sboxes']}",
        f"data size     : {em['data_size']}",
    ])
    if em.get("skipped"):
        lines.append(f"skip reason   : {em['reason']}")
        return lines
    lines.extend([
        (
            "trial 0001: returned={returned} total={total} probability={probability:.12g}".format(
                returned=em["returned_count"],
                total=em["data_size"],
                probability=em["estimated_r"],
            )
        ),
        "-" * 72,
        f"aggregate returned quartets     : {em['returned_count']}",
        f"aggregate total quartets        : {em['data_size']}",
        f"estimated probability           : {em['estimated_r']:.12g}",
        f"log2(probability)               : {format_log2(em['log2_r'])}",
        f"Hadipour interval log2          : [{em['hadipour_lower_log2']:.2f}, {em['hadipour_upper_log2']:.2f}]",
    ])
    return lines


def format_probability_log(probability_result):
    lines = ["", "=" * 72, "搜索后的概率实验"]
    lines.extend(format_diff_section("上路径", probability_result["diff"]["upper"]))
    lines.extend(format_diff_section("下路径", probability_result["diff"]["lower"]))
    lines.extend(format_em_section(probability_result["em_boomerang"]))
    total = probability_result["total_boomerang_probability"]
    lines.extend(["", "-" * 72, "整个 boomerang 区分器实验概率"])
    lines.append("formula       : p^2 * r * q^2")
    if total["status"] == "available":
        lines.append(f"result        : 2^({total['log2_probability']:.6f})")
        lines.append(f"probability   : {total['probability']:.12g}")
    else:
        lines.append(f"status        : unavailable ({total['reason']})")
    return "\n".join(lines) + "\n"


def total_boomerang_probability(diff, em):
    """Combine observed p, q and r without substituting theoretical bounds."""
    upper = diff["upper"]["result"]
    lower = diff["lower"]["result"]
    if upper is None or lower is None:
        return {"status": "unavailable", "reason": "one differential trail is absent"}
    if upper.get("skipped") or lower.get("skipped") or em.get("skipped"):
        return {"status": "unavailable", "reason": "at least one experiment was skipped"}
    p = upper.get("probability", 0.0)
    q = lower.get("probability", 0.0)
    r = em.get("estimated_r", 0.0)
    probability = p * p * r * q * q
    if probability <= 0:
        return {
            "status": "unavailable",
            "reason": "at least one experiment observed zero right pairs/quartets",
            "probability": 0.0,
            "log2_probability": None,
        }
    return {
        "status": "available",
        "formula": "p^2 * r * q^2",
        "p": p,
        "q": q,
        "r": r,
        "probability": probability,
        "log2_probability": math.log2(probability),
    }


def run_probability_checks(result, seed=None):
    seed = today_seed() if seed is None else int(seed)
    diff = evaluate_diff_probability(result, seed)
    em = evaluate_em_boomerang_probability(result, seed)
    return {
        "parameters": {
            "seed": seed,
            "max_data_size": f"2^({MAX_DATA_EXPONENT})",
            "sample_rule": "diff data size = 2^(ceil(weight)+2); Em data size = max(2^12, 2^(2*CAS+2)); skip when data size >= 2^16",
        },
        "diff": diff,
        "em_boomerang": em,
        "total_boomerang_probability": total_boomerang_probability(diff, em),
    }


def save_probability_checks(result, output_dir, seed=None):
    output_dir = Path(output_dir)
    probability_result = run_probability_checks(result, seed)
    (output_dir / "probability_tests.json").write_text(
        json.dumps(probability_result, indent=2),
        encoding="utf-8",
    )
    log_text = format_probability_log(probability_result)
    (output_dir / "probability_tests.txt").write_text(log_text, encoding="utf-8")
    return probability_result, log_text


def _load_json(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _rounds_from_case_name(case_name):
    head = case_name.split("_", 1)[0]
    fields = head.split("-")
    if len(fields) != 3 or not all(field.isdigit() for field in fields):
        raise ValueError(
            f"cannot obtain r0/rm/r1 from result directory name {case_name!r}"
        )
    return {"r0": int(fields[0]), "rm": int(fields[1]), "r1": int(fields[2])}


def _formal_result_files(result_dir):
    case_name = result_dir.name
    preferred = result_dir / f"{case_name}.json"
    if preferred.exists():
        return preferred
    candidates = [
        path for path in result_dir.glob("*.json")
        if path.name not in {
            "exact_summary.json",
            "upper_diff_trail.json",
            "lower_diff_trail.json",
            "rounds.json",
            "probability_tests.json",
            "probability_summary.json",
        }
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise FileNotFoundError(
        "missing formal result JSON; expected "
        f"{preferred.name} or exactly one legacy case JSON"
    )


def load_probability_input_from_results(result_dir):
    """Load a completed search without rebuilding any MILP or SMT model."""
    result_dir = Path(result_dir).resolve()
    if not result_dir.is_dir():
        raise FileNotFoundError(f"result directory does not exist: {result_dir}")
    fallback = None
    try:
        fallback = _load_json(_formal_result_files(result_dir))
    except FileNotFoundError:
        pass

    exact_summary_path = result_dir / "exact_summary.json"
    if exact_summary_path.exists():
        exact = _load_json(exact_summary_path)
        accepted_id = exact.get("accepted_truncated_id")
        if exact.get("result") == "SAT" and isinstance(accepted_id, int):
            path_dir = result_dir / f"truncated_{accepted_id:04d}"
            upper_path = path_dir / "upper" / "accepted.json"
            lower_path = path_dir / "lower" / "accepted.json"
            truncated_path = path_dir / "truncated_path.json"
            missing = [
                str(path) for path in (upper_path, lower_path, truncated_path)
                if not path.exists()
            ]
            if not missing:
                upper = _load_json(upper_path)
                lower = _load_json(lower_path)
                if (
                    upper.get("smt_status") == "SAT"
                    and lower.get("smt_status") == "SAT"
                    and upper.get("concrete_replay") == "PASS"
                    and lower.get("concrete_replay") == "PASS"
                ):
                    source = fallback or {}
                    parameters = dict(source.get("parameters", {}))
                    for name, value in _rounds_from_case_name(result_dir.name).items():
                        parameters.setdefault(name, value)
                    activity = _load_json(truncated_path)["activity"]
                    return {
                        "parameters": parameters,
                        "upper_trail": activity["upper_trail"],
                        "middle_part": activity["middle_part"],
                        "lower_trail": activity["lower_trail"],
                        "diff_upper_trail": upper["characteristic"],
                        "diff_lower_trail": lower["characteristic"],
                        "exact_summary": exact,
                        "trail_source": "exact_accepted",
                        "accepted_truncated_id": accepted_id,
                    }
            if fallback is None:
                detail = ", ".join(missing) if missing else "accepted status/replay is invalid"
                raise RuntimeError(
                    "exact verification result cannot be used and no formal "
                    f"fallback exists: {detail}"
                )

    if fallback is None:
        raise RuntimeError(
            "result directory lacks enough trail data for probability testing"
        )
    required = {
        "parameters", "upper_trail", "middle_part", "lower_trail",
        "diff_upper_trail", "diff_lower_trail",
    }
    missing = sorted(required - set(fallback))
    if missing:
        raise RuntimeError(
            "formal result is incomplete; missing fields: " + ", ".join(missing)
        )
    fallback = dict(fallback)
    fallback["trail_source"] = "formal_result"
    fallback["accepted_truncated_id"] = None
    return fallback
