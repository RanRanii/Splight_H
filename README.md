# Splight-H

本目录实现 Splight 单密钥场景下的 Hadipour 方法版 boomerang / sandwich distinguisher 搜索。参数、MILP 调用方式和输出顺序沿用参考项目，密码传播依据 Splight 轮函数。

## 环境要求

需要 Python、Gurobi/gurobipy；启用 exact verification 时还需要 z3-solver。以下命令均在 Test/Splight-H 目录中执行。

## 基本搜索

默认参数为 r0=2、rm=3、r1=2、w0=6、wm=3、w1=6：

```powershell
python boom.py
```

显式指定参数：

```powershell
python boom.py -r0 2 -rm 3 -r1 2 -w0 6 -wm 3 -w1 6 -tl 1200
```

未开启 exact verification 时，程序执行一次截断搜索和首次 upper/lower concrete Diff 实例化，然后保存结果。

## 独立差分路径搜索

不运行 boomerang 主流程，只搜索指定轮数的单密钥具体差分路径：

```powershell
python diff.py --nrounds 2 --mode 0 --startweight 0 --timelimit 120
```

其中 `--nrounds` 指定轮数，`--direction` 可设为 `forward` 或 `backward`。LP 文件仍按原规则写入 `tmp/boomerang/`，不会改变原有命名方式。

终端输出正常显示，同时保存到：

```text
result_diff/round_2/
  round_2.json
  round_2.txt
  terminal_print.txt
```

其他轮数使用相同规则，例如 `--nrounds 6` 对应 `result_diff/round_6/`。

## Exact 双层搜索

```powershell
python boom.py -r0 2 -rm 3 -r1 2 -w0 6 -wm 3 -w1 6 -tl 1200 --exact-verify
```

带完整时间限制的示例：

```powershell
python boom.py -r0 2 -rm 3 -r1 2 -w0 6 -wm 3 -w1 6 -tl 1200 --global-tl 7200 --exact-verify --exact-verify-timeout-ms 300000 --exact-path-timeout-sec 1800
```

时间参数：

- -tl/--timelimit：单次 MILP optimize 的限制。
- --global-tl：整个搜索阶段的限制，不包含概率实验时间。
- --exact-verify-timeout-ms：单个 Z3 candidate 的限制，默认 300000 毫秒。
- --exact-path-timeout-sec：一条 truncated path 的全部 concrete refinement 限制，默认不限制。

exact 模式先枚举 raw truncated candidate，并试算 upper/lower Diff。只有两侧均可行才分配 truncated ID。内层在同一个 Diff model 上验证 concrete candidate：UNSAT 添加完整 concrete no-good；UNKNOWN 不作为 UNSAT；SAT 且 replay PASS 才正式接受。

## 概率测试

搜索后立即测试：

```powershell
python boom.py -r0 2 -rm 3 -r1 2 -w0 6 -wm 3 -w1 6 --probtest t
```

默认 --probtest f。开启后，seed 使用当天日期；upper/lower data size 为 2^(ceil(weight)+2)；Em 至少测试 2^12 个数据，达到或超过 2^16 时跳过对应实验。Em 只覆盖中间 rm 轮，最终输出 p^2 * r * q^2 的实验结果。

## 离线概率测试

已有结果可以直接测试，不重新运行 truncated MILP、Diff MILP 或 Z3：

```powershell
python run/probtest_from_results.py --result-dir .\results\2-3-2_636
```

可选覆盖 seed：

```powershell
python run/probtest_from_results.py --result-dir .\results\2-3-2_636 --seed 20260909
```

如果存在成功的 exact_summary.json，脚本优先读取其中指定的 upper/accepted.json 和 lower/accepted.json；否则兼容旧版正式 case JSON。输出位于 probability_results/<case>/。

## Full 结果入口

boom_full.py 使用与 boom.py 相同的参数和搜索逻辑，但结果写入 results_full：

```powershell
python boom_full.py -r0 2 -rm 3 -r1 2 -w0 6 -wm 3 -w1 6 -tl 1200 --exact-verify
```

## 结果目录

普通入口写入：

```text
results/<r0>-<rm>-<r1>_<w0><wm><w1>/
```

例如 results/2-3-2_636/。主要文件：

- terminal_print.txt：完整终端输出。
- <case>.json、<case>.txt：正式搜索结果。
- <case>_distinguisher.md、bmd.tex：区分器文档。
- upper_diff_trail.*、lower_diff_trail.*：最终具体差分路径。
- exact_summary.json：exact 双层搜索全局结果。
- truncated_XXXX/：一条 upper/lower 均通过首次 MILP 实例化的联合截断路径。
- probability_tests.*：仅在 --probtest t 时生成。

exact 路径目录结构：

```text
truncated_XXXX/
  truncated_path.json
  truncated_path.md
  summary.json
  upper/
    accepted.json
    rejected/
    unknown/
  lower/
    accepted.json
    rejected/
    unknown/
```

## 代码目录

- boom.py：主入口和双层搜索控制。
- boom_full.py：results_full 输出入口。
- diff.py：bit-level concrete 差分 MILP。
- truncdiff.py、truncboom.py：截断传播和 Hadipour boomerang MILP。
- tools/exact_verify.py：单密钥 Z3 真实性验证与 concrete replay。
- probability/：差分与 Em 概率实验。
- output/：Markdown/TikZ 结果格式化。
- run/：离线概率测试和批量运行脚本。
- tmp/：LP 文件；具体差分 LP 位于 tmp/boomerang/。
