"""Run all Splight-H cases with r0+rm+r1 in {10, 11}.

The script is resumable: completed result JSON files are reused, while missing
or failed cases are executed through boom.py with probability testing enabled.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
RESULTS_DIR = BASE / "results"
SUMMARY_DIR = RESULTS_DIR / "total_10_11_summary"


def combinations(total):
    for r0 in range(1, total - 1):
        for r1 in range(1, total - r0):
            rm = total - r0 - r1
            if rm > 0:
                yield r0, rm, r1


def include_case(r0, rm, r1, scope):
    if scope == "all":
        return True
    if scope == "rm-not-min":
        return rm >= min(r0, r1)
    if scope == "rm-greater-both":
        return rm > r0 and rm > r1
    raise ValueError(f"unknown scope: {scope}")


def all_cases(scope):
    for total in (10, 11):
        for r0, rm, r1 in combinations(total):
            if include_case(r0, rm, r1, scope):
                yield total, r0, rm, r1


def result_path(name):
    return RESULTS_DIR / name / f"{name}.json"


def load_case(r0, rm, r1, status="ok", note=None):
    name = f"{r0}-{rm}-{r1}"
    path = result_path(name)
    if not path.exists():
        return {
            "total": r0 + rm + r1,
            "r0": r0,
            "rm": rm,
            "r1": r1,
            "name": name,
            "status": "missing",
            "note": note or "result json not found",
            "result_dir": f"results/{name}",
        }

    data = json.loads(path.read_text(encoding="utf-8"))
    prob = data.get("probability_tests", {})
    upper = prob.get("diff", {}).get("upper", {}).get("result", {})
    lower = prob.get("diff", {}).get("lower", {}).get("result", {})
    em = prob.get("em_boomerang", {})
    upper_weight = data.get("diff_upper_trail", {}).get("total_weight")
    lower_weight = data.get("diff_lower_trail", {}).get("total_weight")
    cas = data.get("middle_part", {}).get("as")
    total_log2_bound = boomerang_upper_bound_log2(upper_weight, lower_weight, cas)
    return {
        "total": r0 + rm + r1,
        "r0": r0,
        "rm": rm,
        "r1": r1,
        "name": name,
        "status": status,
        "note": note,
        "objective": data.get("middle_part", {}).get("objective"),
        "cas": cas,
        "upper_weight": upper_weight,
        "upper_data_size": upper.get("data_size"),
        "upper_right": upper.get("right"),
        "upper_log2": upper.get("log2_probability"),
        "lower_weight": lower_weight,
        "lower_data_size": lower.get("data_size"),
        "lower_right": lower.get("right"),
        "lower_log2": lower.get("log2_probability"),
        "em_delta": em.get("input_diff_delta"),
        "em_nabla": em.get("output_diff_nabla"),
        "em_data_size": em.get("data_size"),
        "em_skipped": em.get("skipped"),
        "em_returned": em.get("returned_count"),
        "em_log2": em.get("log2_r"),
        "em_skip_reason": em.get("reason"),
        "total_probability_log2_upper_bound": total_log2_bound,
        "total_probability_upper_bound": fmt_probability(total_log2_bound),
        "greater_than_2^-64": total_log2_bound is not None and total_log2_bound > -64,
        "result_dir": f"results/{name}",
    }


def has_probability_result(name):
    path = result_path(name)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return "probability_tests" in data


def run_case(r0, rm, r1, time_limit, case_timeout, force=False):
    name = f"{r0}-{rm}-{r1}"
    if not force and has_probability_result(name):
        return load_case(r0, rm, r1, status="ok", note="reused existing result")

    cmd = [
        sys.executable,
        "boom.py",
        "-r0", str(r0),
        "-rm", str(rm),
        "-r1", str(r1),
        "-w0", "6",
        "-wm", "3",
        "-w1", "6",
        "-tl", str(time_limit),
        "--probtest", "t",
    ]
    try:
        completed = subprocess.run(
            cmd,
            cwd=BASE,
            text=True,
            capture_output=True,
            check=False,
            timeout=case_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        log_text = exc.stdout or ""
        if exc.stderr:
            log_text += "\n[stderr]\n" + exc.stderr
        log_text += f"\n[timeout]\ncase timeout after {case_timeout} seconds\n"
        (SUMMARY_DIR / f"{name}_terminal.txt").write_text(log_text, encoding="utf-8")
        row = load_case(r0, rm, r1, status="timeout", note=f"timeout after {case_timeout}s")
        if row["status"] == "missing":
            row["status"] = "timeout"
        return row

    log_text = completed.stdout
    if completed.stderr:
        log_text += "\n[stderr]\n" + completed.stderr
    (SUMMARY_DIR / f"{name}_terminal.txt").write_text(log_text, encoding="utf-8")

    if completed.returncode != 0:
        row = load_case(r0, rm, r1, status="failed", note=f"returncode {completed.returncode}")
        if row["status"] == "missing":
            row["status"] = "failed"
        row["returncode"] = completed.returncode
        return row

    return load_case(r0, rm, r1)


def fmt_power(value):
    if value is None:
        return "n/a"
    if isinstance(value, str):
        return value
    return f"2^({value.bit_length() - 1})" if value > 0 and value & (value - 1) == 0 else str(value)


def parse_weight(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def boomerang_upper_bound_log2(upper_weight, lower_weight, cas):
    upper = parse_weight(upper_weight)
    lower = parse_weight(lower_weight)
    if upper is None or lower is None or cas is None:
        return None
    return -(2 * upper + 2 * lower + 2 * float(cas))


def fmt_probability(log2_value):
    if log2_value is None:
        return "n/a"
    exponent = -log2_value
    if abs(exponent - round(exponent)) < 1e-9:
        exponent_text = str(int(round(exponent)))
    else:
        exponent_text = f"{exponent:.2f}"
    return f"2^(-{exponent_text})"


def fmt_log2(value):
    return "未观测" if value is None else f"{value:.6f}"


def fmt_count(value):
    return "n/a" if value is None else str(value)


def fmt_status(row):
    status = row.get("status")
    if status == "ok":
        return "完成"
    if status == "timeout":
        return "超时"
    if status == "failed":
        return "失败"
    return status or "未知"


def fmt_threshold(row):
    value = row.get("greater_than_2^-64")
    if value is None:
        return "n/a"
    return "是" if value else "否"


def totals_in_rows(rows):
    return sorted({row["total"] for row in rows}) or [10, 11]


def make_summary(rows, time_limit, case_timeout, scope):
    lines = [
        "# Splight-H total=10/11 全组合搜索与概率测试总结",
        "",
        "本次扫描满足 `r0+rm+r1 in {10, 11}` 且 `r0>0,r1>0,rm>0` 的组合。",
        f"当前筛选范围为 `{scope}`；`rm-not-min` 表示 `rm >= min(r0, r1)`，即中间段不是三段中的最小值。",
        "每个组合调用 `boom.py --probtest t`，单个组合的完整结果目录为 `results/r0-rm-r1/`。",
        "",
        f"- Gurobi TimeLimit: `{time_limit}` 秒",
        f"- 外层单组合超时: `{case_timeout}` 秒",
        "- 已存在且包含 `probability_tests` 的结果会直接复用，便于续跑。",
        "",
    ]
    for total in totals_in_rows(rows):
        subset = [row for row in rows if row["total"] == total]
        lines.extend([
            f"## total={total}",
            "",
            "| r0 | rm | r1 | 状态 | CAS | 上权重 | 上data | 上命中 | 上log2 | 下权重 | 下data | 下命中 | 下log2 | Em data | Em returned | Em log2 | Em跳过 | p^2q^2r上界 | >2^-64 | 结果目录 |",
            "|---:|---:|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---:|:---:|---|",
        ])
        for row in subset:
            if row["status"] != "ok":
                lines.append(
                    f"| {row['r0']} | {row['rm']} | {row['r1']} | {fmt_status(row)} | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `{row['result_dir']}` |"
                )
                continue
            skipped = "是" if row.get("em_skipped") else "否"
            lines.append(
                f"| {row['r0']} | {row['rm']} | {row['r1']} | 完成 | {row['cas']} | "
                f"{row['upper_weight']} | {fmt_power(row['upper_data_size'])} | {fmt_count(row['upper_right'])} | {fmt_log2(row['upper_log2'])} | "
                f"{row['lower_weight']} | {fmt_power(row['lower_data_size'])} | {fmt_count(row['lower_right'])} | {fmt_log2(row['lower_log2'])} | "
                f"{fmt_power(row['em_data_size'])} | {fmt_count(row['em_returned'])} | {fmt_log2(row['em_log2'])} | {skipped} | "
                f"{row.get('total_probability_upper_bound', 'n/a')} | {fmt_threshold(row)} | `{row['result_dir']}` |"
            )
        lines.append("")

        above = [
            row for row in subset
            if row.get("status") == "ok" and row.get("greater_than_2^-64")
        ]
        lines.extend([f"### total={total} 中 `p^2q^2r > 2^-64` 的配置", ""])
        if above:
            configs = ", ".join(
                f"`({row['r0']},{row['rm']},{row['r1']})={row['total_probability_upper_bound']}`"
                for row in above
            )
            lines.append(f"- {configs}")
        else:
            lines.append("- 无。")
        lines.append("")

    observed = [
        row for row in rows
        if row.get("status") == "ok" and not row.get("em_skipped") and (row.get("em_returned") or 0) > 0
    ]
    lines.extend(["## 结论", ""])
    for total in totals_in_rows(rows):
        obs = [row for row in observed if row["total"] == total]
        if not obs:
            lines.append(f"- total={total}：本次抽样未观测到非零 Em boomerang switch。")
        else:
            best_rm = max(row["rm"] for row in obs)
            best_rows = [row for row in obs if row["rm"] == best_rm]
            names = ", ".join(row["name"] for row in best_rows)
            lines.append(f"- total={total}：观测到非零 Em boomerang switch 的最大 `rm` 为 `{best_rm}`，组合：`{names}`。")
        above = [
            row for row in rows
            if row["total"] == total and row.get("status") == "ok" and row.get("greater_than_2^-64")
        ]
        if above:
            names = ", ".join(f"{row['name']}({row['total_probability_upper_bound']})" for row in above)
            lines.append(f"- total={total}：按 `r=2^(-2*CAS)` 上界计算，满足 `p^2q^2r > 2^-64` 的配置为：{names}。")
        else:
            lines.append(f"- total={total}：按 `r=2^(-2*CAS)` 上界计算，没有配置满足 `p^2q^2r > 2^-64`。")

    incomplete = [row for row in rows if row.get("status") != "ok"]
    if incomplete:
        names = ", ".join(f"{row['name']}({fmt_status(row)})" for row in incomplete)
        lines.extend(["", "## 未完成组合", "", f"- {names}"])

    lines.extend([
        "",
        "## 说明",
        "",
        "- 概率测试 data size 规则沿用 `boom.py --probtest t`：diff 为 `2^(ceil(weight)+2)`，Em 为 `max(2^12, 2^(2*CAS+2))`，达到或超过 `2^16` 时跳过。",
        "- 表中 `p^2q^2r上界` 按 `r=2^(-2*CAS)` 计算，即 `p^2q^2r = 2^(-(2*w_upper + 2*w_lower + 2*CAS))`。",
        "- 每个组合的完整终端输出保存在本汇总目录的 `r0-rm-r1_terminal.txt`。",
        "- 每个组合的 JSON、TXT、区分器 Markdown 和 probability 测试文件保存在对应 `results/r0-rm-r1/` 目录。",
    ])
    return "\n".join(lines) + "\n"


def write_split_outputs(rows, time_limit, case_timeout, scope):
    for total in (10, 11):
        total_rows = [row for row in rows if row["total"] == total]
        total_dir = RESULTS_DIR / f"total_{total}_summary"
        total_dir.mkdir(parents=True, exist_ok=True)
        (total_dir / f"total_{total}_raw.json").write_text(
            json.dumps(total_rows, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (total_dir / f"TOTAL_{total}_SUMMARY.md").write_text(
            make_summary(total_rows, time_limit, case_timeout, scope),
            encoding="utf-8",
        )
        above = [
            row for row in total_rows
            if row.get("status") == "ok" and row.get("greater_than_2^-64")
        ]
        (total_dir / f"total_{total}_above_2^-64.json").write_text(
            json.dumps(above, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        above_lines = [
            f"# total={total} 中 p^2q^2r > 2^-64 的配置",
            "",
            "这里的 `r` 采用 Hadipour boomerang 上界 `r=2^(-2*CAS)`，因此 `p^2q^2r = 2^(-(2*w_upper + 2*w_lower + 2*CAS))`。",
            "",
        ]
        if above:
            above_lines.extend([
                "| r0 | rm | r1 | CAS | 上权重 | 下权重 | p^2q^2r上界 |",
                "|---:|---:|---:|---:|---:|---:|---:|",
            ])
            for row in above:
                above_lines.append(
                    f"| {row['r0']} | {row['rm']} | {row['r1']} | {row['cas']} | "
                    f"{row['upper_weight']} | {row['lower_weight']} | {row['total_probability_upper_bound']} |"
                )
        else:
            above_lines.append("无满足条件的配置。")
        (total_dir / f"TOTAL_{total}_ABOVE_2^-64.md").write_text(
            "\n".join(above_lines) + "\n",
            encoding="utf-8",
        )
        log_dir = total_dir / "terminal_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        for row in total_rows:
            source = SUMMARY_DIR / f"{row['name']}_terminal.txt"
            if source.exists():
                shutil.copy2(source, log_dir / source.name)
            else:
                (log_dir / source.name).write_text(
                    "该组合复用了已存在的结果 JSON，但未找到此前保存的 terminal log。\n"
                    f"结果目录：{row['result_dir']}\n",
                    encoding="utf-8",
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--time-limit", type=int, default=180, help="Gurobi TimeLimit passed to boom.py.")
    parser.add_argument("--case-timeout", type=int, default=240, help="Outer timeout for one full boom.py case.")
    parser.add_argument("--force", action="store_true", help="Rerun cases even if probability result JSON exists.")
    parser.add_argument(
        "--scope",
        choices=["all", "rm-not-min", "rm-greater-both"],
        default="rm-not-min",
        help="Case filter. Default follows the latest request: rm is not the minimum segment.",
    )
    args = parser.parse_args()

    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for _total, r0, rm, r1 in all_cases(args.scope):
        name = f"{r0}-{rm}-{r1}"
        print(f"running {name}", flush=True)
        rows.append(run_case(r0, rm, r1, args.time_limit, args.case_timeout, force=args.force))

    (SUMMARY_DIR / "total_10_11_raw.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    summary_path = SUMMARY_DIR / "TOTAL_10_11_SUMMARY.md"
    summary_path.write_text(make_summary(rows, args.time_limit, args.case_timeout, args.scope), encoding="utf-8")
    write_split_outputs(rows, args.time_limit, args.case_timeout, args.scope)
    print(summary_path)


if __name__ == "__main__":
    main()
