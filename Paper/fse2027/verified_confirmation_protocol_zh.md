# SWE-bench Verified 确认实验协议（冻结前设计）

这份协议把开发集观察到的“修复能力不下降、交互成本可能降低”拆成可反驳的确认性实验。开发集结果不进入 Verified 主队列；所有运行使用 DeepSeek Flash、temperature=0，并在揭示官方结果前封存补丁和决策轨迹。

## 主问题

在相同案例、模型、公开 issue、源码基线和资源机会下，证据问题调度是否达到不低于强基线的 SWE-bench Verified Resolved@1，同时减少全尝试模型请求和 provider token？

主成本终点为完整 provider `total_tokens`；模型调用数为关键次要终点。未知 usage 记录不能填零，若仍缺失只能报告上下界，不能声称成本下降。

## 队列与隔离

- 数据集固定为 SWE-bench Verified 500 条 parquet（记录 SHA-256）。
- 从历史开发、诊断和调参暴露清单中排除案例，再按 repository 轮询随机选取 100 条作为主确认队列。
- generation JSONL 只含 issue、公开 hints、repo、base commit 和环境字段；gold patch、FAIL_TO_PASS、PASS_TO_PASS 和 test patch 保留在密封后的官方副本中。
- 每个案例先在对应 base commit 创建干净 detached worktree；每个 arm 独立 worktree、缓存和请求预算。
- 只有所有 cell 的 patch hash 封存后，才允许打开官方测试字段并运行 harness。

## 主臂

| 臂 | 设定 | 解释 |
|---|---|---|
| F00 | 固定交互、关闭证据复用 | 强内部基线 |
| F10 | 按需交互、关闭证据复用 | 判断按需调度的主效应 |
| F01 | 固定交互、开启证据复用 | 判断复用主效应 |
| F11 | 按需交互、开启证据复用 | 完整方案 |

首轮按 40 calls、250,000 admission tokens、900 秒和最多 2 次 revision 运行，作为低预算点；随后在预先固定的 30 个 Verified 子集上运行 80/500,000/1,800 秒和 160/1,000,000/3,600 秒预算曲线。预算曲线不能根据单个案例结果调整。

## 必须的控制

在独立 Verified 子集上增加：

1. `Recovery-only`：仅在缺失 executable target 时执行 recovery，关闭 question-triggered falsification。
2. `Budget-only`：采用开发集冻结的阶段 cap，不读取 unresolved question 状态。
3. `Flat-exact-cache`：保留相同 source/patch/environment/oracle key，但不重新打开 evidence-question 依赖。
4. `Graph-control-off`：保留相同源码和验证器，只关闭图驱动的定位/挑战排序；图仍记录执行供审计，但不控制动作选择。

这些臂必须使用相同 compiler、source discovery、repair player、公开输入和预算；若控制实现改变了模型可见源码或 patch generator，必须单列为不同宿主比较。

## 局部因果干预

从 trusted closure、evidence blocked、action exhausted、budget termination 四类停止点预先分层抽样。对每个 sealed snapshot 随机分配：一支 suppress 同一动作，另一支 force 执行同一动作；其余预算和状态保持一致。记录新 evidence、是否改变 action、是否发现 trusted counterexample、是否改变最终候选和官方结果。

另给停止点 10 calls、100,000 tokens、600 秒的隔离 continuation。continuation 不能替换主实验 patch；它只估计错误停止率和可恢复成功率。

## 统计与报告

- 每个 issue 是配对 block；多次重复不能当作独立 issue。
- 主结果报告 Resolved@1、paired wins/losses/ties、generation failure、harness error、完整 calls/tokens、每 resolved 成本和端到端时延。
- 能力使用预注册非劣界限 `delta`，单侧 95% issue-block 区间；不以“不显著”代替非劣证明。
- 成本使用 issue-block bootstrap，给 ratio-of-totals 和每案例分布；多消融使用 Holm 校正，探索性结果单独标记。
- 报告 repository 分层、leave-one-repository-out、预算命中率、图 CPU/wall/memory/serialization 开销。
- 安全指标包括 stale reuse、false suppression、missed trusted counterexample、confirmed preservation regression 和 stop-after-continuation success。

只有当完整方案在预注册的能力门槛内、完整成本确实更低、没有新增高权限 preservation regression，并且节省可由局部证据干预回指时，才能声称“同等修复能力下减少交互成本”。
