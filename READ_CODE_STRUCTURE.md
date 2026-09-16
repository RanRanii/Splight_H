# Splight-H 代码结构说明

## 核心入口

`boom.py` 是主入口。它读取 `r0/rm/r1/w0/wm/w1/tl` 等参数，调用 `TruncatedBoomerang` 搜索截断 sandwich 路径，再用 `Diff` 实例化上、下具体差分路径。结果统一写入 `results/r0-rm-r1/`。

保留在根目录的核心搜索文件：

- `boom.py`
- `diff.py`
- `truncdiff.py`
- `truncboom.py`

这些文件是搜索框架的主体，不再继续下沉到子目录。

## Splight 基础实现

`splight/` 存放 Splight 算法和约束基础：

- `splight/splight_spec.py`：S-box、线性层、SHI2、状态转换函数。
- `splight/splight_cipher.py`：零密钥/给定轮密钥的 reduced-round 加解密实现。
- `splight/sbox_constraints.py`：DDT、权重表、S-box 支持约束。

`diff.py` 从这里读取 S-box 和 DDT 约束。

## 概率测试

`probability/` 存放概率实验相关代码：

- `probability/probability_evaluator.py`：由 `boom.py --probtest t` 调用，负责上/下差分概率和 Em boomerang switch 概率测试。
- `probability/eval_em.py`：中间 `Em` boomerang switch 的 Monte Carlo 实验。
- `probability/eval_default_diff_probability.py`：早期/独立差分概率测试辅助脚本。

Em 测试只覆盖中间 `rm` 轮，不测试整段 `r0+rm+r1`。

## 输出格式化

`output/plotdistinguisher.py` 负责生成 Markdown 区分器文件，包括截断路径、common active S-box、具体差分路径和 Mermaid 图。

## 批量运行

`run/` 存放运行辅助脚本：

- `run/run_rm_sweep.py`：固定 `r0=2,r1=2` 扫描 `rm`。
- `run/run_em_only_sweep.py`：扫描 `0-n-0` 的 Em-only switch 情况。
- `run/boom copy.py`：历史副本，保留但不作为主入口。

从根目录运行脚本示例：

```bash
python run/run_em_only_sweep.py
```

## 文件输出约定

- 主 LP：`tmp/splight_h_{r0}_{rm}_{r1}.lp`
- 具体差分 LP：`tmp/boomerang/`
- 搜索结果：`results/r0-rm-r1/`
- 批量汇总：各运行脚本在 `results/` 下创建独立汇总目录或汇总文件
