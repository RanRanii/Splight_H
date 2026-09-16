"""运行 r0=1, r1=1, rm=2..9 的搜索并汇总概率测试结果。"""

import json
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
SUMMARY_DIR = BASE / "results" / "one_one_1_n_1_summary"


def run_case(rm):
    name = f"1-{rm}-1"
    cmd = [
        sys.executable,
        "boom.py",
        "-r0", "1",
        "-rm", str(rm),
        "-r1", "1",
        "-w0", "6",
        "-wm", "3",
        "-w1", "6",
        "-tl", "1200",
        "--probtest", "t",
    ]
    completed = subprocess.run(
        cmd,
        cwd=BASE,
        text=True,
        capture_output=True,
        check=False,
    )
    log_text = completed.stdout
    if completed.stderr:
        log_text += "\n[stderr]\n" + completed.stderr
    (SUMMARY_DIR / f"{name}_terminal.txt").write_text(log_text, encoding="utf-8")
    if completed.returncode != 0:
        return {"rm": rm, "name": name, "status": "failed", "returncode": completed.returncode}

    result_path = BASE / "results" / name / f"{name}.json"
    data = json.loads(result_path.read_text(encoding="utf-8"))
    prob = data.get("probability_tests", {})
    upper = prob.get("diff", {}).get("upper", {}).get("result", {})
    lower = prob.get("diff", {}).get("lower", {}).get("result", {})
    em = prob.get("em_boomerang", {})
    return {
        "rm": rm,
        "name": name,
        "status": "ok",
        "cas": data["middle_part"].get("as"),
        "upper_input": data["diff_upper_trail"]["x_0"],
        "upper_output": data["diff_upper_trail"][f"x_{data['diff_upper_trail']['nrounds']}"],
        "upper_weight": data["diff_upper_trail"]["total_weight"],
        "upper_data_size": upper.get("data_size"),
        "upper_right": upper.get("right"),
        "upper_log2": upper.get("log2_probability"),
        "lower_input": data["diff_lower_trail"]["x_0"],
        "lower_output": data["diff_lower_trail"][f"x_{data['diff_lower_trail']['nrounds']}"],
        "lower_weight": data["diff_lower_trail"]["total_weight"],
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
        "result_dir": f"results/{name}",
    }


def fmt_log2(value):
    return "未观测" if value is None else f"{value:.6f}"


def fmt_count(value):
    return "n/a" if value is None else str(value)


def make_summary(rows):
    lines = [
        "# Splight-H 1-n-1 搜索与概率测试总结",
        "",
        "本次固定 `r0=1`、`r1=1`，扫描 `rm=2..9`，并对每个结果开启 `--probtest t`。",
        "",
        "## 汇总表",
        "",
        "| rm | CAS | 上差分 data | 上命中 | 上 log2 | 下差分 data | 下命中 | 下 log2 | Em data | Em returned | Em log2 | Em 跳过 | 结果目录 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---|",
    ]
    for row in rows:
        if row["status"] != "ok":
            lines.append(
                f"| {row['rm']} | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 是：失败 | `results/{row['name']}` |"
            )
            continue
        skipped = "是" if row.get("em_skipped") else "否"
        lines.append(
            f"| {row['rm']} | {row['cas']} | {row['upper_data_size']} | {fmt_count(row['upper_right'])} | "
            f"{fmt_log2(row['upper_log2'])} | {row['lower_data_size']} | {fmt_count(row['lower_right'])} | "
            f"{fmt_log2(row['lower_log2'])} | {row['em_data_size']} | {fmt_count(row['em_returned'])} | "
            f"{fmt_log2(row['em_log2'])} | {skipped} | `{row['result_dir']}` |"
        )

    lines.extend(["", "## Em 连接差分", ""])
    lines.extend([
        "| rm | Delta | nabla | skip reason |",
        "|---:|---|---|---|",
    ])
    for row in rows:
        if row["status"] != "ok":
            lines.append(f"| {row['rm']} | n/a | n/a | failed |")
            continue
        lines.append(
            f"| {row['rm']} | `{row['em_delta']}` | `{row['em_nabla']}` | {row.get('em_skip_reason') or ''} |"
        )

    observed = [
        row for row in rows
        if row.get("status") == "ok" and not row.get("em_skipped") and (row.get("em_returned") or 0) > 0
    ]
    max_round = max((row["rm"] for row in observed), default=None)
    lines.extend(["", "## 结论", ""])
    if max_round is None:
        lines.append("在本次抽样规则下，没有观测到非零 Em boomerang switch。")
    else:
        lines.append(f"在 `rm=2..9` 范围内，实际抽样观测到非零 Em boomerang switch 的最大中间轮数为 `{max_round}`。")
    return "\n".join(lines) + "\n"


def main():
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for rm in range(2, 10):
        print(f"running 1-{rm}-1", flush=True)
        rows.append(run_case(rm))
    (SUMMARY_DIR / "one_one_1_n_1_raw.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    summary_path = SUMMARY_DIR / "ONE_ONE_1_N_1_SUMMARY.md"
    summary_path.write_text(make_summary(rows), encoding="utf-8")
    print(summary_path)


if __name__ == "__main__":
    main()
