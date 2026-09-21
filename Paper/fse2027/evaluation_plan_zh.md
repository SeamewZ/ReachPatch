# ReachPatch：投稿级实验与消融设计

## 1. 要证明什么，而不是承诺录用

核心命题：**在预先规定的修复能力非劣界限内，版本化证据问题驱动的调度，降低端到端模型交互成本。**

这不是“图越大越好”“生成更多 patch”“调用变少所以成功”。FSE 2027 CFP 的标准是原创性、重要性、可靠性、评测、表达和相关工作比较，没有一个达到就保证录用的性能数字。当前 20 例快照不足以证明核心命题；英文稿是有明确证据边界的研究初稿，不是可直接提交的完成稿。

必须串起四段证据：

1. 哪些工作在相同相关状态下重复发生，浪费多大。
2. 哪个版本化问题让控制器省去一次具体操作，或在代码变化后重新执行它。
3. 省去操作之后，隐藏评测中的修复能力是否保持。
4. 净收益是否超过构图、失效检查、执行、重试和上下文管理开销。

## 2. 已有结果允许怎么写

冻结截止点：2026-09-20 09:42 UTC。20 例按 baseline 完成时间进入样本，只统计最终保留的生成尝试。

| 指标 | 同代码固定交互 | 当前方案 | 观察到的降幅 |
| --- | ---: | ---: | ---: |
| 模型调用 | 439 | 323 | 26.4% |
| total tokens | 6,264,375 | 4,428,109 | 29.3% |

不能写“保持准确率的同时降低 29.3% 成本”：缺 baseline 官方结果；失败重试未计入；历史方法组重试不统一；成本是 token 不是金额；不是随机同期实验。不能补一个置信区间就消除这些偏差。

方法组另有 49 例记录：879 calls，12,049,596 tokens；544 次调用用于 recovery。132 次 source reuse，但 context elision、validation cache hit、duplicate-action-prevented 的记录均为 0。这说明**机制存在不等于机制贡献已验证**。不要把所有降幅归功于“证据图去重”。

P0/final 的官方记录分别为 14/49 和 9/49，但包含 7/11 个基础设施错误；共同完成的 31 例为 9→9。5 个表面退步对应相同 patch hash 与 final harness error，不能认定为代码回退。这也不是 baseline/方案的正确率对照。

## 3. 第一层：最小完整主实验

### 固定协议

- 开发集与评测集分离。已反复调试的 20/49/50 例均属于开发或 pilot，不再包装为未见评测。
- 优先在 SWE-bench Verified 完整 500 例中明确扣除开发重叠，再在未见部分做冻结评测；公开完整列表、排除理由和数据 hash。若报告官方全 500 分数，另外明确标注其中开发污染部分，不能与干净泛化结果混为一谈。
- 增加一个时间上较新的、可执行的 Python issue cohort。记录 issue 创建时间、模型版本和公开测试可见性；未知训练数据使“无污染”不能得到证明。
- 不混用 Lite 与 Verified 的重叠案例增加样本量。跨语言需要实际适配 AST/trace 后另做，当前实现不能声称通用跨语言。
- 至少两种模型家族：当前 DeepSeek Flash 为主，另选稳定且可冻结版本的强 coding 模型。记录实际 API model ID、服务端版本（若有）、temperature、thinking、缓存计费策略、工具规范。无 seed 支持就明说，不能把 temperature=0 当确定性。
- 同一 issue 的实验臂随机交错启动，固定 worker 数、镜像、CPU/内存、公开测试、预算上限；记录运行日期、负载与服务错误。避免“昨天方法组、今天 baseline”的系统偏差。
- 每例每臂一次完整运行为主要单位；在功效与预算允许时做 3 次独立重复，报告稳定性。重复仍嵌套在 issue 内，不当作全新独立案例。
- 每次只 seal 一个最终 patch，之后统一官方 harness。生成失败也 seal 空 patch 并留在分母。不得根据 hidden outcome 重试生成或挑最终 patch。

### 对照选择

| 对照 | 控制因素 | 回答什么 |
| --- | --- | --- |
| 同代码固定交互 | 相同图、修复器、工具、预算；关闭按需与复用 | 当前策略相对自己的母系统是否有益 |
| Agentless 或一个冻结版本的简洁 repair agent | 同模型与尽可能一致的信息/预算，保留对方真实算法 | 是否只是战胜过度复杂的自建 baseline |
| AgentDiet | 在同一可兼容宿主中比较，完整计入 reflection 成本 | 是否优于或补充轨迹压缩 |
| 当前方案 + AgentDiet | 不改 recovery/oracle 权限，保留压缩额外成本 | 减少操作与压缩历史是否互补 |

“直接 Flash”必须说明给多少代码、如何检索、允许什么工具、如何输出 diff。若给它人工完美定位，其信息条件已不同。模型名称本身不是实验算法。论文中的公开历史分数不能充当同条件复现。

### 主要指标

| 指标 | 定义与分母 | 作用 |
| --- | --- | --- |
| 官方 Resolved@1 | 最终选定 patch resolved 数 / 所有预先登记 issue 数 | 修复能力主指标 |
| 全尝试 tokens | 所有阶段、模型、失败请求与重试的 provider usage 之和；未知单列 | 成本主指标 |
| 全尝试模型调用 | 发出的请求数，成功/失败/未知分别记录 | 交互强度主指标 |
| 实际金额 | cached input、uncached input、output、reasoning 按冻结价目计费 | 商业可用性 |
| 每 resolved 的成本 | cohort 总成本 / resolved 数；分母为零则未定义 | 不被“放弃难题”美化 |
| 端到端时延 | 每例开始到 patch seal；均值、中位数、P90/P95 | 用户等待成本 |
| 执行与图开销 | 工具 CPU/墙时、构图/更新/查询耗时、峰值内存 | 是否转嫁模型成本给执行成本 |
| 能力—成本曲线 | 多个预先固定预算点上的 Resolved@1 与实际支出 | 是否产生有价值的 Pareto 改善 |

tokens 减少不一定等于调用减少，调用减少不一定等于工具轨迹减少，三者都不一定等于等待时间减少。必须分别报告。

## 4. 第二层：可归因的消融实验

先做 2×2 因子实验；全部使用相同统一图，不能关闭图时连源码检索与公开测试也一起删掉。

| 实验臂 | 按需调度 D | 证据复用 R |
| --- | ---: | ---: |
| F00 | 0 | 0 |
| F10 | 1 | 0 |
| F01 | 0 | 1 |
| F11 | 1 | 1 |

分别报告 D 主效应、R 主效应以及交互，不只报告 F00→F11。随后拆开：

| 消融 | 保留什么 | 要验证的因果解释 |
| --- | --- | --- |
| 去掉 action suppression | 保留调度、读证据与验证 | 是否确实避免同版本同问题的再尝试 |
| 去掉 source reuse | 保留相同读取内容与调度 | 节省传输/提示，还是只有对象缓存 |
| 去掉 context elision | 保留模型消息顺序和其余机制 | 精确重复 payload 消除是否真的减少 tokenizer tokens |
| 去掉 validation cache | 相同执行预算与 oracle | 是否节省执行；命中为零则如实报告无可测收益 |
| deterministic recovery 对照 | 按需条件不变，只改变已知操作由程序还是模型调度 | 不把程序接管执行的收益归因于图 |
| question-driven vs 固定相同调用数的停止器 | 相同调用量分布或预设 cap，无 evidence-aware 停止 | 是否只是调用上限变小 |
| flat exact cache | 相同 source/patch/env key，删除 question dependency 生命周期 | 图中关系提供了什么额外决策能力 |
| 关系表等价实现 | 保留完全相同依赖与失效语义，仅换存储 | “图”的存储形态是否只是工程选择 |
| 全量失效 vs 当前版本失效 | 所有证据变化都重开 vs 记录依赖版本 | 失效粒度是否既可靠又节省 |

没有命中的模块不能靠 toy 激活就宣称在真实仓库有收益。建议先做 30–50 个**新开发例**的 instrumented activation audit，决定哪些机制值得进入昂贵全量消融；该开发审计不充当最终无偏结果。

不要给每个节点/字段单独消融。消融单位应是一个可被反驳的机制假设，而不是代码函数目录。

## 5. 第三层：证明省掉的是重复工作，而不是必要工作

### 操作与成本归因

事件链记录：`question/version → action → model request/tool call → observation → question transition → checkpoint/stop`。

每个 request 记录 request ID、phase、question ID/version、action ID、parent/checkpoint hash、prompt source IDs、模型/版本、usage、状态、时延；失败请求 usage 不可得时标为 unknown，不写 0。每个 tool action 记录 command/args hash、patch hash、environment identity 和结果语义签名，敏感环境只存 hash。

指标及其边界：

- **State-conditioned repeat rate**：同相关状态和操作身份下的重复次数 / 可分类操作总数。单纯相同字符串作为“语法重复率”另报。
- **Duplicate-only round rate**：一轮模型输出的工具操作全部已见且无新状态。不是 empty response，也不自动等于无价值推理。
- **Skipped-action attribution**：被省去的动作关联到已回答/已尝试问题的比例；不凭模型声称“没必要”。
- **Question closure yield**：每个阶段、每万 token 新关闭的可信问题数；重新打开后又关闭不能无限刷计数。问题粒度固定，避免拆小问题美化。
- **Reopen rate / stale reuse rate**：代码、oracle、environment 变化后应重新打开而未打开的次数；有独立参考执行才能认定 stale。
- **Target recovery rate**：获得 authority-grounded、进入指定 target、clean 稳定失败的 executable scenario 的 issue 比例。将 import/setup failure 分开。
- **Challenge yield**：生成→可执行→有 oracle→执行→稳定→区分候选→改变选择，逐级给分母。
- **Decision-relevant evidence rate**：证据导致下一动作、候选拒绝/保留或停止状态改变的比例；这是可审计关联，因果作用仍需干预。
- **Graph causal-use rate**：存在实际影响 localization/scheduling/validation 的记录，并通过 replay/control 验证；节点数或单次读取图不算有效贡献。

### 受控继续执行

在封存主结果后，按预先抽样方案选择不同停止原因的案例，从相同状态给独立诊断分支 5/10 个额外调用（参数在看 outcome 前确定）。

如果继续执行找到了可信 counterexample，或生成了 hidden harness 可接受的更好 patch，标为该继续策略下的 missed opportunity。主实验 patch 不替换。比较方案组与固定调用 cap 的 missed opportunity，判断 evidence-aware stopping 是否有信息优势。

有限继续执行没有发现修复，不能证明该动作在任何策略下都无用。人工标注也要有两名标注者、明确规则、分歧裁决和一致性报告，而不是另一个 LLM 随意打 utility 分。

## 6. 统计检验与样本量

主结论需要两个条件同时成立：

1. `ΔResolved = p_method - p_baseline` 的单侧置信下界高于预注册 `-δ`。
2. 全尝试成本差异的区间支持有意义的降低，而非只看点估计。

`δ` 是实际可接受能力损失，不是调到显著为止的参数。例如 2 个百分点可以作为讨论起点，但是否合理须由用途决定，不能认定 500 例必定够。按 paired discordance rate 与预期效果做模拟功效分析，保留 issue 内多次运行相关性。若功效不足，结论是 inconclusive，不是“能力相同”。

报告配对成功/失败四格表、wins/losses、paired confidence interval；McNemar 可用于差异的补充检验，但 nonsignificant 不等于 non-inferiority。成本报告 aggregate ratio 与 paired absolute difference 的 issue-cluster bootstrap 区间，重复运行跟随 issue 一起抽样。仓库数很少时，仓库层 bootstrap 也不稳，应补 per-repository 和 leave-one-repository-out 敏感性分析。

多个次要消融预先指定 family 并控制多重比较；探索性分析明确标注，不在多组结果中只挑最好组。极端长尾既报 trimmed sensitivity，也保留未裁剪的主要成本结果。

## 7. 运行前必须补齐的实现与质量检查

这些是实验前置条件，不是本次撰稿已经实现的改动：

1. 修复生产/测试中 execution identity 缺 project module files 的已知失败，并冻结实现。当前不能写“全部测试通过”。
2. action claim 在执行前记录：注入暂时性 timeout、网络失败、进程中止，验证是否误将未完成动作当已完成并永久 suppression。必要时区分 claimed/running/completed/retryable。
3. 检查所有生产阶段是否实际经过 question gate；记录 bypass，避免只对某一 recovery 分支生效。
4. 完成 all-attempt ledger；历史缺 usage 只能报告下界，不能精确比较。新实验每次请求结束即持久化。
5. 对语法相同但状态改变的测试、同命令不同 env、相同 diff 不同 base、None/empty Oracle、NameError 作为 expected exception 做失效/权限测试。
6. 图预算耗尽不得漏验证；UNKNOWN/BLOCKED 不可当 PASS；trusted input oracle 与 exploratory input 必须分开。
7. 修复与验证请求中的敏感字段、原始环境不能进入公开 artifact；保留可复现必要的非敏感环境 manifest。

## 8. 最终论文应该出现的表图

- 表 1：数据、信息权限、模型、预算、版本、重试/失败处理协议。
- 表 2：主方法与强 baseline 的 Resolved@1、全部成本、calls、latency、CIs、infrastructure errors。
- 表 3：2×2 因子效应与细粒度机制消融，包含 activation counts。
- 图 1：一个问题从 source evidence、repair 到 boundary challenge、reopen/stop 的统一图示例。
- 图 2：多个预算下的 resolution—cost 曲线，而非孤立的 token 柱状图。
- 图 3：按阶段划分的 call/token 成本与去掉的具体操作类型。
- 表 4：停止错误、stale reuse、oracle 误绑定、恢复失败的分层误差分析。
- 定性案例：真实成功节省、真实错误停止、必须重新验证各一个，全部关联可审计 artifact。

## 9. 可以支持投稿主张的证据门槛

这不是会议录用承诺，而是内部可信度门槛：

- 未见 cohort 上，修复非劣与净成本下降同时得到区间支持。
- 不止战胜自建固定冗余 baseline；至少认真比较 AgentDiet 与一个简洁强修复系统。
- 2×2 消融证明收益来源；flat-cache/control 实验证明需要什么语义，不神化图存储。
- recovery-only 停止不能包办全部收益；错误停止风险有实测边界。
- 全尝试 usage 可核对，官方评测错误已处理，结果与封存 patch 一一对应。
- 两模型或另一未见 cohort 上能重复趋势；适用范围和失败条件明确。

若只有当前 20 例的两个降幅，论文仍不足以支持主张。若最终主要收益只是 deterministic recovery 或较低调用 cap，应诚实收缩方法和标题，不把它包装成统一图的已验证贡献。
