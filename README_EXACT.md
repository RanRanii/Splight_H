# Splight-H Exact Verification 与运行说明

默认主入口为 boom.py。输出 case 名称为 r0-rm-r1_w0wmw1，写入 results/<case>/；boom_full.py 使用同一逻辑，写入 results_full/<case>/。

## 两种主流程

未开启 --exact-verify：

1. 搜索一条截断 boomerang 路径。
2. 对该路径分别实例化首次 upper 和 lower concrete 差分。
3. 保存正式 trail、Markdown、TikZ 摘要和终端输出。

开启 --exact-verify：

1. 外层复用同一个截断 MILP，枚举完整截断 activity path。
2. 先试算 upper 与 lower 的首次 Diff 实例化。任一不可行时，此 raw path 不计入 results，只添加 raw truncated no-good。
3. 两侧均可行后才创建 truncated_XXXX 目录。
4. 在固定截断路径中复用同一个 Diff MILP。Z3 判为 UNSAT 时，对当前完整 concrete difference signature 添加 no-good 后再次 optimize。
5. Z3 返回 SAT 且 concrete replay 为 PASS 时接受；返回 UNKNOWN 时不排除 concrete candidate，当前截断路径记录为 TIMEOUT_SKIPPED。
6. 当前路径 concrete candidate 耗尽后，给完整 truncated activity signature 加 no-good，并在同一截断 MILP 上搜索下一路径。

因此结果状态严格区分 CONCRETE_EXHAUSTED、TIMEOUT_SKIPPED、NO_EXACT_REALIZABLE_TRAIL 和 SEARCH_INCOMPLETE_TIMEOUT。

## 时间限制

-tl 或 --timelimit 限制单个 MILP solve。

--global-tl 限制整个搜索阶段。

--exact-verify-timeout-ms 限制单个 Z3 candidate。

--exact-path-timeout-sec 可选地限制一个固定截断路径的全部 concrete refinement。

--probtest t 在搜索结束后运行，不计入 --global-tl。

## Exact 结果目录

每个 MILP-qualified path 写入 results/<case>/truncated_XXXX/：

- truncated_path.json 和 truncated_path.md：联合截断路径。
- summary.json：该路径的 upper/lower refinement 状态。
- upper/ 和 lower/：accepted、rejected、unknown candidate。

根目录 exact_summary.json 指向最终 accepted truncated ID。只有 accepted upper/lower concrete trail 才会写入正式 upper_diff_trail 和 lower_diff_trail 文件。

## 离线概率测试

执行：

    python run/probtest_from_results.py --result-dir .\results\2-3-2_636

脚本不构建 truncated MILP、Diff MILP 或 Z3。若存在成功的 exact_summary.json，则使用被它指定的 accepted upper/lower JSON；否则读取正式 case JSON。输出写入 probability_results/<case>/，包含 terminal_print.txt、probability_tests.txt 和 summary.json。
