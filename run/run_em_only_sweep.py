"""运行 r0=0, r1=0, rm=2..9 的 Em-only boomerang switch 扫描。"""

import json
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
SUMMARY_DIR = BASE / "results" / "em_only_0_n_0_summary"


def run_case(rm):
    cmd = [
        sys.executable,
        "boom.py",
        "-r0", "0",
        "-rm", str(rm),
        "-r1", "0",
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
    (SUMMARY_DIR / f"0-{rm}-0_terminal.txt").write_text(log_text, encoding="utf-8")
    if completed.returncode != 0:
        return {"rm": rm, "status": "failed", "returncode": completed.returncode}

    result_path = BASE / "results" / f"0-{rm}-0" / f"0-{rm}-0.json"
    data = json.loads(result_path.read_text(encoding="utf-8"))
    em = data.get("probability_tests", {}).get("em_boomerang", {})
    return {
        "rm": rm,
        "status": "ok",
        "cas": data["middle_part"].get("as"),
        "delta": em.get("input_diff_delta"),
        "nabla": em.get("output_diff_nabla"),
        "data_size": em.get("data_size"),
        "skipped": em.get("skipped"),
        "returned": em.get("returned_count"),
        "estimated_r": em.get("estimated_r"),
        "log2_r": em.get("log2_r"),
        "upper_log2": em.get("hadipour_upper_log2"),
        "lower_log2": em.get("hadipour_lower_log2"),
        "result_dir": f"results/0-{rm}-0",
    }


def make_summary(rows):
    lines = [
        "# Splight-H Em-only 0-n-0 搜索总结",
        "",
        "本次固定 `r0=0`、`r1=0`，扫描 `rm=2..9`。",
        "由于没有上/下具体差分路径，Em 测试使用截断边界 active mask 映射为具体差分：活跃 nibble 取 `1`，非活跃 nibble 取 `0`。",
        "",
        "| rm | CAS | Delta | nabla | data size | returned | log2(r) | 是否跳过 | 结果目录 |",
        "|---:|---:|---|---|---:|---:|---:|:---:|---|",
    ]
    for row in rows:
        if row["status"] != "ok":
            lines.append(
                f"| {row['rm']} | n/a | n/a | n/a | n/a | n/a | n/a | 是：失败 | `results/0-{row['rm']}-0` |"
            )
            continue
        log2_value = "未观测" if row.get("log2_r") is None else f"{row['log2_r']:.6f}"
        skipped = "是" if row.get("skipped") else "否"
        returned = "n/a" if row.get("returned") is None else str(row["returned"])
        lines.append(
            f"| {row['rm']} | {row['cas']} | `{row['delta']}` | `{row['nabla']}` | "
            f"{row['data_size']} | {returned} | {log2_value} | {skipped} | `{row['result_dir']}` |"
        )

    observed = [
        row for row in rows
        if row.get("status") == "ok" and not row.get("skipped") and (row.get("returned") or 0) > 0
    ]
    max_round = max((row["rm"] for row in observed), default=None)
    lines.extend(["", "## 结论", ""])
    if max_round is None:
        lines.append("在本次扫描和抽样规则下，没有观测到非零 Em boomerang switch。")
    else:
        lines.append(f"在 `rm=2..9` 范围内，实际抽样观测到非零 Em boomerang switch 的最大中间轮数为 `{max_round}`。")
    lines.extend([
        "",
        "## 说明",
        "",
        "- `CAS` 是截断 Hadipour 模型的 common active S-box 数量。",
        "- `Delta` 来自上截断路径在 Em 输入端的 active mask；`nabla` 来自下截断路径在 Em 输出端的 active mask。",
        "- Em 测试 data size 至少为 `2^12`，若按 `2^(2*CAS+2)` 得到更大值则使用更大值；达到或超过 `2^16` 时跳过。",
        "- 每个 `results/0-n-0/` 目录中保留完整 JSON、TXT、区分器 Markdown 和 probability 测试文件。",
    ])
    return "\n".join(lines) + "\n"


def main():
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for rm in range(2, 10):
        print(f"running 0-{rm}-0", flush=True)
        rows.append(run_case(rm))
    (SUMMARY_DIR / "em_only_0_n_0_raw.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    summary_path = SUMMARY_DIR / "EM_ONLY_0_N_0_SUMMARY.md"
    summary_path.write_text(make_summary(rows), encoding="utf-8")
    print(summary_path)


if __name__ == "__main__":
    main()
