"""Splight-H 结果的文本、Markdown 和 TikZ 风格格式化工具。"""

import json


def mask_to_concrete_hex(mask):
    return "".join("1" if ch != "0" else "0" for ch in mask)


def load_result(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def ascii_trails(result):
    sol = result.get("truncated_search", {}).get("solution", result)
    lines = ["上路径："]
    for key, value in sol.get("upper_trail", {}).items():
        lines.append(f"  {key}: {value}")
    lines.append("中间 common active S-box：")
    for key, value in sol.get("middle_part", {}).items():
        lines.append(f"  {key}: {value}")
    lines.append("下路径：")
    for key, value in sol.get("lower_trail", {}).items():
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def save_ascii(result_path, output_path):
    text = ascii_trails(load_result(result_path))
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    return text


def _round_table(trail, nrounds, include_pr=False):
    if include_pr:
        header = "| 轮数 | x | y | l | RK | ak | z | pr |"
        sep = "|---:|---|---|---|---|---|---|---|"
    else:
        header = "| 轮数 | x | y | l | z |"
        sep = "|---:|---|---|---|---|"
    rows = [header, sep]
    for r in range(nrounds + 1):
        if include_pr:
            rows.append(
                f"| {r} | `{trail.get(f'x_{r}', 'none')}` | `{trail.get(f'y_{r}', 'none')}` | "
                f"`{trail.get(f'l_{r}', 'none')}` | `{trail.get(f'rk_{r}', '00000000')}` | "
                f"`{trail.get(f'ak_{r}', trail.get(f'l_{r}', 'none'))}` | `{trail.get(f'z_{r}', 'none')}` | "
                f"`{trail.get(f'pr_{r}', 'none')}` |"
            )
        else:
            rows.append(
                f"| {r} | `{trail.get(f'x_{r}', 'none')}` | `{trail.get(f'y_{r}', 'none')}` | "
                f"`{trail.get(f'l_{r}', 'none')}` | `{trail.get(f'z_{r}', 'none')}` |"
            )
    return "\n".join(rows)


def mermaid_distinguisher(result):
    params = result["parameters"]
    r0, rm, r1 = params["r0"], params["rm"], params["r1"]
    du = result.get("diff_upper_trail") or {}
    dl = result.get("diff_lower_trail") or {}
    middle = result.get("middle_part") or {}
    delta = du.get(f"x_{r0}") or mask_to_concrete_hex(result["upper_trail"].get(f"x_{r0}", ""))
    nabla = dl.get("x_0") or mask_to_concrete_hex(result["lower_trail"].get("x_0", ""))
    input_diff = du.get("x_0") or delta
    output_diff = dl.get(f"x_{r1}") or nabla
    return "\n".join([
        "```mermaid",
        "flowchart LR",
        f'  P["输入差分\\n{input_diff}"] --> E0["E0：{r0} 轮\\n上差分路径"]',
        f'  E0 --> D["进入 Em 的 Delta\\n{delta}"]',
        f'  D --> EM["Em：{rm} 轮\\nCAS={middle.get("as", "unknown")}"]',
        f'  EM --> N["Em 输出端的 nabla\\n{nabla}"]',
        f'  N --> E1["E1：{r1} 轮\\n下差分路径反向视角"]',
        f'  E1 --> C["输出差分\\n{output_diff}"]',
        "```",
    ])


def probability_estimate_lines(result):
    """Return the theoretical probability summary printed by boom.py."""
    upper_effect = float(result.get("diff_effect_upper_log2", 0))
    lower_effect = float(result.get("diff_effect_lower_log2", 0))
    common_active = float((result.get("middle_part") or {}).get("as", 0))
    total_weight = 2.0 * upper_effect + 2.0 * lower_effect
    lower_bound = total_weight - 2.5 * common_active
    upper_bound = total_weight - 2.0 * common_active
    return [
        f"differential effect of the upper trail: 2^({upper_effect:.2f})",
        f"differential effect of the lower trail: 2^({lower_effect:.2f})",
        "Total probability = p^2*q^2*r = "
        f"2^({2.0 * upper_effect:.2f}) x 2^({2.0 * lower_effect:.2f}) x r",
        f"2^({lower_bound:.2f}) <= Total probability <= 2^({upper_bound:.2f})",
        "To compute the accurate value of total probability, r should be evaluated "
        "experimentally or using the (F)BCT framework",
    ]


def markdown_distinguisher(result):
    params = result["parameters"]
    r0, rm, r1 = params["r0"], params["rm"], params["r1"]
    lines = [
        f"# Splight-H 区分器 r0={r0}, rm={rm}, r1={r1}",
        "",
        "## Boomerang 概率预估",
        "",
        "```text",
        *probability_estimate_lines(result),
        "```",
        "",
        mermaid_distinguisher(result),
        "",
        "## 上截断路径",
        "",
        _round_table(result["upper_trail"], r0 + rm),
        "",
        "## 中间 common active S-box",
        "",
        f"common active S-box 数量：`{result['middle_part'].get('as', 'unknown')}`",
        "",
    ]
    for key in sorted(k for k in result["middle_part"] if k.startswith("s_")):
        lines.append(f"- `{key}`: `{result['middle_part'][key]}`")
    lines.extend([
        "",
        "## 下截断路径",
        "",
        _round_table(result["lower_trail"], rm + r1),
        "",
        "## 上具体差分路径（E0）",
        "",
        (
            _round_table(result["diff_upper_trail"], result["diff_upper_trail"]["nrounds"], include_pr=True)
            if result.get("diff_upper_trail") is not None
            else "无 E0 具体差分路径；使用上截断边界 active mask 映射为 Em 的 Delta。"
        ),
        "",
        "## 下具体差分路径（E1）",
        "",
        (
            _round_table(result["diff_lower_trail"], result["diff_lower_trail"]["nrounds"], include_pr=True)
            if result.get("diff_lower_trail") is not None
            else "无 E1 具体差分路径；使用下截断边界 active mask 映射为 Em 的 nabla。"
        ),
        "",
    ])
    return "\n".join(lines)


def save_markdown_distinguisher(result_path, output_path):
    result = load_result(result_path)
    text = markdown_distinguisher(result)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return text


def tex_init():
    return "% Hadipour 风格搜索生成的 Splight-H 区分器\n"


def tex_fin(nrounds):
    return f"% {nrounds} 轮 Splight-H 区分器结束\n"


def tex_middle(upper_trail, midd_trail, lower_trail, r0, rm, r1):
    lines = [
        "% 截断 boomerang 中间部分摘要",
        f"% r0={r0}, rm={rm}, r1={r1}",
        "% 上路径",
    ]
    for key, value in upper_trail.items():
        lines.append(f"% {key}: {value}")
    lines.append("% 中间 common active S-box")
    for key, value in midd_trail.items():
        lines.append(f"% {key}: {value}")
    lines.append("% 下路径")
    for key, value in lower_trail.items():
        lines.append(f"% {key}: {value}")
    return "\n".join(lines) + "\n"


def tikz_mark_input_bits(active_input_bits, color="red"):
    return f"% 输入活动 bit {active_input_bits}, 颜色={color}\n"


def tikz_mark_output_bits(active_output_bits, color="blue"):
    return f"% 输出活动 bit {active_output_bits}, 颜色={color}\n"


def tex_diff_trail(trail, markpattern="markupperpath", direction="->"):
    return f"% 差分路径 {markpattern} {direction}: {trail}\n"


def tex_diff_lower_trail(trail, upper_crossing_difference=None, markpattern="marklowerpath", direction="<-"):
    return f"% 下差分路径 {markpattern} {direction}: {trail}\n"
