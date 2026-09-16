# Splight-H 的 Em 理论概率说明

本文说明 Hadipour 方法版 Splight boomerang / sandwich 搜索中，中间部分 `Em` 的概率含义和当前代码的计算方式。

## 轮数划分

区分器按下面方式划分：

```text
E = E1 o Em o E0
rounds = r0 + rm + r1
```

`boom.py` 只为 `E0` 和 `E1` 实例化具体的 bit-wise 差分路径。中间 `Em` 在截断 boomerang MILP 中用概率为 1 的正向/反向截断传播和 common active S-box 变量建模。

具体的中间连接实验使用：

```text
Delta = 上差分 E0 路径的输出差分
      = diff_upper_trail[x_r0]

nabla = 下差分 E1 路径在 Em 输出状态处的输入差分
      = diff_lower_trail[x_0]
```

实验测量的中间 switch 概率为：

```text
r = Pr[
    Em^{-1}(Em(x) xor nabla)
    xor Em^{-1}(Em(x xor Delta) xor nabla)
    = Delta
]
```

## Hadipour 的 Common-Active 估计

截断 sandwich 模型统计中间部分的 common active S-box 数量：

```text
CAS(Em) = common active S-box 变量 s_r_j = 1 的个数
```

按照原 Hadipour 代码的风格，中间概率通常用 `CAS(Em)` 给出区间估计：

```text
2^(-2.5 * CAS(Em)) <= r <= 2^(-2 * CAS(Em))
```

这个区间不是 Splight 专用的精确 switch 概率，而是用于搜索和排序截断 sandwich 路径的 common-active S-box 估计。

## 实验概率和理论区间为什么会不同

`results/RM_SWEEP_SUMMARY.md` 中的实验 `r` 是对固定 `Delta` 和 `nabla` 做 Monte Carlo 得到的直接估计：

```text
returned_count / trials
```

它可能与基于 `CAS` 的区间不同，主要原因包括：

1. `CAS` 只统计 common active S-box 的位置，不区分这些 S-box 上具体的输入/输出差分。
2. Splight 的轮函数是 `S-L-S`，线性层会在第二层 S-box 之前耦合不同 S-box 的差分。
3. Feistel 交换和 `SHI2` 旋转会限制反向传播后差分重新相遇的位置。
4. 固定的输入/输出差分可能对应多条兼容差分路径，所以实测差分效应可能大于单条最佳特征的概率。

因此，汇总表中的 `Em test log2` 应理解为真实 Splight 实现上的抽样观测值；`Em theory interval log2` 是 Hadipour 模型给出的粗粒度理论区间。

## 精确理论计算路线

如果要精确计算 `r`，需要组合 `Em` 的完整中间转移关系：

1. 构造 Splight S3 的 S-box DDT 和 BCT。
2. 将 S-box 表提升到 Splight 的两层轮函数 `S-L-S`。
3. 按 Feistel 更新和 `SHI2` 置换组合 `rm` 轮。
4. 对所有从 `Delta` 到 `nabla` 的兼容中间 switch 路径求和。

对于 64-bit 状态，直接枚举这个完整关系规模过大。当前代码因此同时报告两类结果：

- Hadipour 模型给出的 `CAS` 理论区间。
- 使用真实 Splight 实现得到的直接实验估计。

判断具体中间连接是否非零时，应优先查看实验估计：只要 `Em` 的命中次数大于 0，对应连接就在抽样中被观测为非零。
