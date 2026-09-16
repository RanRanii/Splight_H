"""运行 r0=2, r1=2, rm=2..6 的实验并汇总概率。"""

import argparse
import contextlib
import io
import json
import math
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
PROB_TEST_DIR = ROOT / "Splight_implement" / "implement_py" / "probability_tests"
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(PROB_TEST_DIR))

from probability.eval_em import estimate_em_probability  # noqa: E402
from output.plotdistinguisher import save_markdown_distinguisher  # noqa: E402
from probability_test import run_experiment  # noqa: E402


def log2_or_none(value):
    return None if value <= 0 else math.log2(value)


def run_with_captured_stdout(func, *args, **kwargs):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = func(*args, **kwargs)
    return result, buffer.getvalue()


def run_boom(rm, time_limit):
    cmd = [
        sys.executable,
        "boom.py",
        "-r0", "2",
        "-rm", str(rm),
        "-r1", "2",
        "-w0", "6",
        "-wm", "3",
        "-w1", "6",
        "-tl", str(time_limit),
    ]
    completed = subprocess.run(
        cmd,
        cwd=BASE,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed


def evaluate_diff_probability(result, seed, batch_size):
    experiments = {}
    for name, trail in (("upper", result["diff_upper_trail"]), ("lower", result["diff_lower_trail"])):
        rounds = int(trail["nrounds"])
        exp_result, stdout = run_with_captured_stdout(
            run_experiment,
            overrides={
                "mode": "diff",
                "rounds": rounds,
                "input_diff": trail["x_0"],
                "output_diff": trail[f"x_{rounds}"],
                "trials": 1,
                "batch_size": batch_size,
                "seed": seed,
            },
        )
        exp_result["log2_probability"] = log2_or_none(exp_result["probability"])
        exp_result["theory_log2"] = -float(trail["total_weight"])
        exp_result["theory_probability"] = 2 ** exp_result["theory_log2"]
        exp_result["delta_log2_exp_minus_theory"] = (
            None
            if exp_result["log2_probability"] is None
            else exp_result["log2_probability"] - exp_result["theory_log2"]
        )
        experiments[name] = {"result": exp_result, "stdout": stdout}
    return experiments


def evaluate_em_probability(result, seed, trials):
    params = result["parameters"]
    r0 = int(params["r0"])
    rm = int(params["rm"])
    common_active = int(result["middle_part"]["as"])
    delta = result["diff_upper_trail"][f"x_{r0}"]
    nabla = result["diff_lower_trail"]["x_0"]
    em = estimate_em_probability(delta, nabla, rm, trials, seed=seed)
    em.update({
        "delta_from_upper_output": delta,
        "nabla_from_lower_input": nabla,
        "rm": rm,
        "common_active_sboxes": common_active,
        "hadipour_upper_log2": -2.0 * common_active,
        "hadipour_lower_log2": -2.5 * common_active,
    })
    if em["estimated_r"] > 0:
        em["delta_log2_vs_upper_bound"] = em["log2_r"] - em["hadipour_upper_log2"]
        em["delta_log2_vs_lower_bound"] = em["log2_r"] - em["hadipour_lower_log2"]
    else:
        em["delta_log2_vs_upper_bound"] = None
        em["delta_log2_vs_lower_bound"] = None
    return em


def append_probability_log(log_text, diff_experiments, em_result):
    log_text += "\n" + "=" * 72 + "\n"
    log_text += "搜索后的概率实验\n"
    for name in ("upper", "lower"):
        log_text += "\n" + "-" * 72 + "\n"
        label = "上路径" if name == "upper" else "下路径"
        log_text += f"{label}差分概率测试\n"
        log_text += diff_experiments[name]["stdout"]
    log_text += "\n" + "-" * 72 + "\n"
    log_text += "Em 中间 boomerang switch 概率测试\n"
    log_text += json.dumps(em_result, indent=2) + "\n"
    return log_text


def make_summary(rows):
    lines = [
        "# Splight-H rm 扫描实验总结",
        "",
        "固定参数：`r0=2`，`r1=2`，`w0=6`，`wm=3`，`w1=6`。",
        "",
        "差分概率使用 `Diff` 找到的 `E0` 和 `E1` 具体 bit-wise 差分路径。",
        "Em 概率实验使用 `Delta = diff_upper_trail[x_r0]` 和 `nabla = diff_lower_trail[x_0]` 作为中间 switch 的输入。",
        "",
        "| rm | 目标值/AS | CAS(Em) | 上路径理论 log2 | 上路径测试 log2 | 上路径差值 | 下路径理论 log2 | 下路径测试 log2 | 下路径差值 | Em 理论区间 log2 | Em 测试 log2 | Em 与区间差异 | Em 是否 > 0 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in rows:
        em = row["em"]
        upper = row["diff"]["upper"]["result"]
        lower = row["diff"]["lower"]["result"]
        upper_delta = upper["delta_log2_exp_minus_theory"]
        lower_delta = lower["delta_log2_exp_minus_theory"]
        if em["log2_r"] is None:
            em_delta = "n/a"
        elif em["log2_r"] < em["hadipour_lower_log2"]:
            em_delta = f"低 {em['log2_r'] - em['hadipour_lower_log2']:.4f}"
        elif em["log2_r"] > em["hadipour_upper_log2"]:
            em_delta = f"高 +{em['log2_r'] - em['hadipour_upper_log2']:.4f}"
        else:
            em_delta = "区间内"
        lines.append(
            "| {rm} | {obj} | {cas} | {ut:.2f} | {ue} | {ud} | {lt:.2f} | {le} | {ld} | [{el:.2f}, {eu:.2f}] | {er} | {ed} | {pos} |".format(
                rm=row["rm"],
                obj=row["objective"],
                cas=em["common_active_sboxes"],
                ut=upper["theory_log2"],
                ue="未观测到" if upper["log2_probability"] is None else f"{upper['log2_probability']:.4f}",
                ud="n/a" if upper_delta is None else f"{upper_delta:+.4f}",
                lt=lower["theory_log2"],
                le="未观测到" if lower["log2_probability"] is None else f"{lower['log2_probability']:.4f}",
                ld="n/a" if lower_delta is None else f"{lower_delta:+.4f}",
                el=em["hadipour_lower_log2"],
                eu=em["hadipour_upper_log2"],
                er="未观测到" if em["log2_r"] is None else f"{em['log2_r']:.4f}",
                ed=em_delta,
                pos="是" if em["returned_count"] > 0 else "否",
            )
        )
    lines.extend([
        "",
        "## 结果解释",
        "",
        "- `上/下路径理论 log2` 是 MILP 给出的单条最优差分特征概率，即 `2^-weight`。",
        "- `上/下路径差值` 是 `实验 log2(概率) - 理论 log2(单条特征概率)`。正数表示固定输入/输出差分的实测 differential effect 大于单条特征估计。",
        "- `上/下路径测试 log2` 来自随机数据实验；它统计固定输入/输出差分下的总 differential effect，因此可能高于单条 characteristic。",
        "- `Em 理论区间 log2` 是 Hadipour common-active S-box 估计，近似使用 `2^(-2.5*CAS) <= r <= 2^(-2*CAS)`。",
        "- `Em 与区间差异` 表示实测 Em switch 概率在 Hadipour 区间内、低于下界还是高于上界。",
        "- `Em 测试 log2` 是直接 boomerang switch 实验结果，用来判断具体中间连接是否实际观测为非零。",
    ])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="运行 Splight-H 的 rm 扫描实验。")
    parser.add_argument("--rm-min", type=int, default=2)
    parser.add_argument("--rm-max", type=int, default=6)
    parser.add_argument("--time-limit", type=int, default=1200)
    parser.add_argument("--diff-batch-size", default="2^10")
    parser.add_argument("--em-trials", type=int, default=2**14)
    parser.add_argument("--seed", type=int, default=20260630)
    args = parser.parse_args()

    result_dir = BASE / "results"
    result_dir.mkdir(exist_ok=True)
    rows = []

    for rm in range(args.rm_min, args.rm_max + 1):
        print(f"=== 正在运行 r0=2, rm={rm}, r1=2 ===")
        completed = run_boom(rm, args.time_limit)
        log_text = completed.stdout
        if completed.stderr:
            log_text += "\n[stderr]\n" + completed.stderr
        if completed.returncode != 0:
            (result_dir / f"2-{rm}-2.txt").write_text(log_text, encoding="utf-8")
            raise RuntimeError(f"boom.py 在 rm={rm} 时失败；请查看 results/2-{rm}-2.txt")

        result_path = BASE / "results" / f"2-{rm}-2" / f"2-{rm}-2.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        snapshot_json = result_dir / f"2-{rm}-2.json"
        snapshot_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

        diff_experiments = evaluate_diff_probability(result, args.seed + rm, args.diff_batch_size)
        em_result = evaluate_em_probability(result, args.seed + rm, args.em_trials)
        log_text = append_probability_log(log_text, diff_experiments, em_result)
        (result_dir / f"2-{rm}-2.txt").write_text(log_text, encoding="utf-8")

        plot_path = result_dir / f"2-{rm}-2_distinguisher.md"
        save_markdown_distinguisher(snapshot_json, plot_path)

        rows.append({
            "rm": rm,
            "objective": result["middle_part"].get("objective", "n/a"),
            "result": result,
            "diff": diff_experiments,
            "em": em_result,
        })
        # boom.py 当前没有把 Gurobi 目标值写入 JSON；这里从终端输出中解析。
        for line in completed.stdout.splitlines():
            if line.startswith("Number of active S-boxes:"):
                rows[-1]["objective"] = line.split(":", 1)[1].strip()

    (result_dir / "rm_sweep_raw.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (result_dir / "RM_SWEEP_SUMMARY.md").write_text(make_summary(rows), encoding="utf-8")


if __name__ == "__main__":
    main()
