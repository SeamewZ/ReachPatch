# FSE 研究实验协议：问题驱动交互能否安全地减少修复成本？

状态：**部分实施，开发预检与 live API smoke 执行中；确认性实验尚未完成**。本协议扩展 evaluation_plan_zh.md，把研究问题落实为对照、函数职责、日志和判定规则。不能把 planned experiments 写成 completed results。具体冻结配置以各 study 的 protocol.json 为准。

2026-09-20 执行备注：已实现逐请求持久化 admission/终态、失败 usage 的 UNKNOWN 标记、四臂分块随机调度、全队列封存 gate、issue-block 成本 bootstrap，以及独立的 suppression/source reuse/compaction/validation/recovery/falsification 开关。最新冻结生产测试集 277 项通过，165 项 legacy_graph 按仓库配置未运行。首轮预检发现 PATH 丢失 rg 和 visible test 命令解析错误，已中止该轮、保留成本、修正后另开版本。修正后的12次 live smoke 全部通过独立公开验收；30开发案例×四臂已经启动，尚未完成。尚未完成 flat-exact、外部 AgentDiet/Agentless、停止后干预、未见数据主实验、第二模型和非劣确认；不得将这份设计中的接口清单视为这些实验已实现或已运行。

## 1. 论文主张与可反驳预测

主张不是“统一图更复杂所以更好”，而是：版本化证据问题能识别不值得重复的交互，在预先规定的修复非劣界限内减少全流程支出。

三个可反驳预测：

- P1：相同预算上限下，全方案的官方修复率非劣，实际全尝试调用/token/费用更低。
- P2：相对于同成本停止策略，问题驱动调度留下更少的可避免修复遗漏，且节省能回指具体动作/问题状态。
- P3：相对于精确缓存和仅按需 recovery，证据依赖与按需反证还有独立价值。

如果只观察到固定 recovery 少跑六轮，应收缩贡献为 recovery orchestration，不宣称整个统一图都有效。若关系表保留相同依赖语义就达到同样结果，这是合理的：存储形式不是独立创新。不能为了保住“图”字制造弱对照。

## 2. 当前实现与实验能力审计

已经有：

- ReachAvoidConfig 的 evidence_reuse_enabled 与 demand_driven_interaction_enabled。
- graph_policy 中的开放动作推导、确定性排序、exact patch transposition。
- evidence_context 中的问题版本、动作 admission、读证据与重复 payload 处理。
- CaseBudget / BudgetedTransport 的请求与 token 预算。
- reachavoid_51.runner 的生成、封存、harness、报告流程。
- run_evidence_ablation.py 的公开 toy 四臂 smoke。
- run_matched_efficiency_baseline.py 的历史 cohort 对照与部分 all-attempt 汇总。

还不能承担论文主实验的地方：

1. toy 四臂不是仓库级随机对照，不能直接用它证明 Resolved@1 非劣。
2. 49 例 baseline 保留图，只关闭两个效率开关；不是“无图”也不是“裸模型”。
3. CaseBudget 的事件主要在内存中积累，controller 结束时落盘；进程提前死亡可使成本缺失。
4. 当前 duplicate tool 身份主要是 stage/name/raw arguments；代码改变后同命令可能是必要复测。
5. evidence_reuse 总开关同时影响多条路径，无法独立测量各机制。
6. action 在执行前标 ATTEMPTED，需要单独验证 transient failure 后是否错误地阻止重试。
7. 旧报表中的 reserved/charged tokens 不是精确 provider usage，必须分列，缺失不能当零。
8. 原 pilot 中 context elision、validation cache 和 action-suppression 计数为零。不能把这些零命中模块当作既有降幅原因。

本次不修改正在使用的修复算法；先写协议，再修计量/开关/已知正确性问题，冻结新版本后重跑。

## 3. 五个研究问题与实验设计

### RQ1：在相同资源机会下，是否保留修复能力并降低净成本？

主要对照：同代码固定交互（F00）、完整方案（F11）、冻结版本的 Agentless 或简洁强 agent。最近邻效率对照必须包括 AgentDiet，但应先完成宿主兼容性验证，不能把不兼容的 wrapper 当作原方法。

AgentDiet 做同宿主的四组比较：宿主原版、宿主+AgentDiet、宿主+本方案、宿主+两者。把 reflection 的模型请求、tokens、时间全部计入。若本方案不能在外部宿主直接移植，明确这是统一宿主上的适配实验，同时保留原生外部 baseline 的单独比较。

输出：official Resolved@1、paired wins/losses、所有尝试的模型费用/tokens/calls、每 resolved 成本、wall time。必须同时展示能力和成本，不单独排 token 排名。

主要效应按原始分布估计；另给预算敏感性曲线：

| 预算级别 | 调用上限 | token admission 上限 | 每例 wall 上限 |
| --- | ---: | ---: | ---: |
| Low | 40 | 250,000 | 900 s |
| Medium | 80 | 500,000 | 1,800 s |
| High | 160 | 1,000,000 | 3,600 s |

这些是开发阶段确认可运行后冻结的**建议值**，不是已运行设置，也不要求每个系统花光预算。图、执行、初始化成本计入各自实际支出。第三方系统无法遵循某一维预算时，须报告差异，不能称严格同预算。还要给实际预算命中率，避免所有臂都被错误 token 估算提前截断。

### RQ2：减少来自按需调度还是证据复用？

使用同一实现的 2×2 因子实验：

| 实验臂 | D：按需交互 | R：复用机制 | 目的 |
| --- | ---: | ---: | --- |
| F00 | 关闭 | 关闭 | 母系统 |
| F10 | 开启 | 关闭 | D 的独立作用 |
| F01 | 关闭 | 开启 | R 的独立作用 |
| F11 | 开启 | 开启 | 联合效果与交互 |

四臂必须共享确定性 compiler、source discovery、public checks、model、patch generator、预算与失败处理。不要给 F00 附加无法合理辩护的重复测试，把 baseline 做弱。固定交互策略需要独立写清并在开发集调好，不能为了凸显结果临时提高重复次数。

对 outcome 和 cost 分别计算 D/R 对比与交互；成本差值的交互可定义为 C11-C10-C01+C00。比例/对数对比另报，不混成一个结论。非劣主要比较固定 F11 vs F00，不能事后挑最容易通过的臂。

### RQ3：具体哪个机制必要，图的依赖语义是否有额外价值？

在开发审计中先测激活，再在独立冻结子集上做确认性消融：

- F11 − action suppression。
- F11 − source evidence reuse。
- F11 − duplicate-payload compaction。
- F11 − validation cache。
- F11 − demand-driven falsification（仅保留按需 recovery）。
- F11 与“仅 deterministic recovery，不做问题驱动调度”比较。

另设三个有力控制：

1. **Flat exact cache**：保留 source/patch/environment/key 的相同身份信息，但不用问题状态和依赖重新打开。
2. **Recovery-only gate**：只在 target 缺失时开启 recovery，其他动作采用固定流程。若达到同样效果，说明贡献可能不是一般问题调度。
3. **Budget-only stopping**：用开发集冻结的 cap 或阶段上限控制调用；不使用问题状态。测试“有信息的停止”是否优于“单纯少调用”。不得根据测试组实际成本逐例回调其 cap。

关系表等价实现只作为表示/工程控制；若行为一致，应报告等价，不期待它失败。对“移除版本失效”这种可能明显不安全的臂，仅用于隔离的离线 stress/replay，不作为部署推荐。

**两种估计分开：**

- End-to-end：每臂独立从 issue 开始，包括 P0，评估整体能力和成本。
- Shared-prefix：固定同一 sealed P0、已有公共证据、初始状态，再分支运行 F00/F11 等，评估 P0 后调度效果。prefix 成本统一计入总体比较，另报增量成本；不能免费给某一臂更好 P0。为版本/权限兼容验证恢复出的 snapshot 状态一致。

Shared-prefix 控制初始 patch 随机性，但不能替代端到端实验，也不能直接从事后完成轨迹推断没执行动作的成功概率。

### RQ4：省去的操作是否必要？停止/复用是否安全？

封存主实验 patch 后，分层抽样以下停止点：trusted closure、evidence blocked、action exhausted、预算终止。对样本在隔离分支继续提供预先固定的 10 次模型调用、100k tokens、600 s，主结果绝不替换。

报告：继续后发现可信反例的比例、产生官方通过 patch 的比例、停止前已存在但未被选中的正确候选比例（所有候选须在揭示 hidden 结果前先封存）。有限继续未发现修复不等于省去操作永远无用。

对省略动作做小型直接干预：从同一 snapshot，一个分支采用 suppress，另一个 force 执行该**同一动作**，不改其他 policy，记录新证据与后续结果。这是随机化的局部机制实验，不把模型自报 utility 当真值。

单独做确定性 stress suite：相同命令但 patch 改变、env 改变、oracle 改变、trace adapter 改变、初次 timeout 后恢复、相同 diff 不同 base、graph budget 满、UNKNOWN/BLOCKED、expected NameError。指标为 stale reuse、false suppression、非法 authority promotion 和 wrong-stop 次数。任何错误都保留可最小化的触发序列。

人工轨迹审计采用预注册随机分层样本，建议 120–200 个 decision windows，由两名标注者独立分类：必要重复、可避免重复、新证据探索、基础设施失败、无法判断。报告一致性、裁决与置信区间。该规模不是统计功效保证，按真实事件发生率调整。

### RQ5：泛化、开销及失败条件是什么？

主模型使用 DeepSeek Flash，严格记录实际服务端 model ID。若扩展到第二模型，先确认预算与 API；在第二模型/独立时间 cohort 上重复 F00/F11 与强 baseline。不同模型的历史 leaderboard 不作为复现结果。

按缺失 target、issue reproduction 完整度、跨文件改动、repository、初始 patch 是否正确、图预算是否耗尽分层。分层标签定义在看结果前冻结；hidden 信息仅用于事后诊断，不回流生成。

测图创建/更新/查询 wall time、CPU、峰值内存、序列化体积、测试执行总时长和端到端时延，报告 P50/P90/P95 与冷/热环境差异。并发执行秒数相加不等于 elapsed wall time。不要没有能源计量设备/估算协议就声称节能。

## 4. 数据、规模与随机化

历史开发案例已被反复看过，必须建立 development-exposure manifest：不只是这 49 个，还包括以前 diagnostic、toy 和调参时看过的真实 issue。按 issue ID 及仓库/问题来源去重。

推荐按以下顺序：

1. 历史开发集上修计量和实现；30 个开发例 smoke 不纳入最终验证。
2. 未见、按 repository/issue 特征分层随机的 100 例做机制实验；在看 outcome 前冻结子集及臂。
3. SWE-bench Verified 未开发重叠部分做主要 F00/F10/F01/F11 与最强可复现 baseline；若同时给 full-500 分数，单列开发重叠，不当干净泛化分数。
4. 新时间 cohort（建议 100–200 例，按可执行性预先筛选而非结果筛选）或第二模型重复确认。

主比较建议三次独立运行；资源有限可对全量一次、预先固定重复子集三次，但必须把外推不确定性写清。若使用同 prefix，按问题/重复把随机性配对。temperature=0 不等于无随机性。

每个 `(issue, repetition, budget)` 是随机化 block，随机打乱 arm 顺序，并平衡跨 block 的顺序。不能先跑完整个 baseline 再跑方法。每个 arm 独立 workspace、cache、budget；除 shared-prefix 实验明确约定的信息，不跨臂共享修复经验或 execution cache。保留 model/provider drift 与系统负载日志。

推荐预算分配优先级：主效应全量 > 有激活的机制消融 > 最近邻比较 > second model > 扩展全部组合。不要把 8 个消融 × 3 个预算 × 多模型全部盲目笛卡尔积展开。总费用按开发数据的**全尝试**均值与尾部估计，并设置实验级硬上限，未知 provider usage 留缺失而非估成免费。

## 5. 主要/次要指标及精确定义

主指标预注册为：(a) official Resolved@1 非劣，(b) 全尝试 token 或金额下降。选择其中一个作为主要成本终点，另一个为关键次要终点；不要结果出来后切换。

- Resolved@1 = 一次 run 最终 sealed patch 通过数 / 所有登记问题数。内部多候选不把指标改成 pass@k；重复运行先分别报告再汇总。
- 总成本 = 所有 stages × 所有 attempts × 所有 requests。failed/timeouts/retries 与 reflection 均计入。
- 已报 tokens、预算 reservation、未知 usage 请求数分列；缺失 usage 不填零，无法完整计费时报告下界/区间和账单核对。
- token reduction = 1 − 全方案总 tokens / baseline 总 tokens；per-case reduction 分布另报。
- 每 resolved 成本 = 全 cohort 成本 / resolved 数；resolved 为零则未定义。
- State-conditioned duplicate rate = 相同 operation identity 与相关 source/patch/env/oracle 状态下重复的 action 数 / 可分类 action 数。不能把每次相同 pytest 都算浪费。
- Duplicate-only round 与 empty response 分开；有重复工具但输出了新推理并不自动属于无效轮次。
- Reuse coverage = 能构造可信 reuse key 的操作 / 全部该类操作；hit rate = hits / eligible operations。两者都报，不能只选有命中的分母。
- Context reduction 以实际 tokenizer/provider input 对照计量；raw UTF-8 bytes 只作辅助，不能直接等同 tokens。
- Target recovery 漏斗：proposed→executable→entered target→grounded oracle→clean stable failure→用于候选验证。每级独立分母。
- Challenge 漏斗：generated→executable→trusted→executed→discriminating→changed decision。
- Safety：stale reuse、false suppression、停止后可恢复成功率、trusted counterexample missed、confirmed preservation regression。
- Selection accuracy：只在**生成候选中至少有一个官方通过候选**的问题上，最终选中通过候选的比例；同时给候选覆盖率，不能条件分母掩盖生成失败。候选 hidden 评测仅在全体封存后离线执行。

所有机制计数与因果贡献分开：graph 被访问次数不证明有效，suppression event 不等于省了一次模型请求。准确的请求成本差异需局部干预/端到端对照。

## 6. 统计分析与结论规则

先规定实际可接受非劣界限 δ。2 个百分点只是一个可讨论的产品容忍界限，不能因为“顶会”就随意选；正式 protocol 必须说明理由。

检验 `H0: p_F11 − p_baseline ≤ −δ`，使用配对二元差异的 score/exact interval 或经模拟校准的方法；单侧 95% 下界高于 −δ 才支持非劣。不显著的 McNemar/superiority 结果不能证明能力相同。

功效不能靠“跑 500 肯定够”。例如零真实损失、配对 discordance≈0.10、δ=0.02 时，粗略正态近似 `n≈(1.645+0.842)^2×0.10/0.02²≈1547`；只是 planning approximation，真实样本量须按重复相关性/仓库结构模拟。增加同一 issue 的重复不等于增加同样多独立问题。功效不足时报告 inconclusive，不能事后放宽 δ。

成本使用 issue-block bootstrap：每次抽一个 issue 的所有 arms/repetitions，一起重算 ratio-of-totals，报告 95% CI、paired difference、median、尾部。仓库少时不能仅凭 cluster asymptotic p 值，补 per-repository/leave-one-repository-out。预注册主要比较，多消融次要比较采用 Holm 等校正；探索性分析显式标注。

官方 evaluation error 不当 confirmed failure；按统一预定 policy 对**相同 sealed patch**最多两次 infra retry。最终仍缺失时给 resolution 上下界及最坏配对非劣敏感性分析，不能只用 completed subset 宣称保持能力。generation failure 留在分母并记真实成本。

## 7. 具体实现：新增与修改的职责

以下是待实现接口，不是已经存在的函数；不要先提交空壳。每个模块与测试、真实调用点同时落地。

### 7.1 独立研究运行器：Code/experiments/evidence_study/

`protocol.py`

- `load_protocol(path) -> StudyProtocol`：校验 schema、必填配置、数据 split、全部臂、预算、重试、δ、主要终点、模型权限。
- `validate_arm_delta(reference, trial)`：白名单差异只允许指定的 intervention；隐藏的工具/预算/可见测试差异直接拒绝。
- `freeze_protocol(protocol) -> manifest`：数据、生产代码、prompt/tools、模型配置、分析计划 hash；运行开始后变化必须是新 study ID。

`schedule.py`

- `build_block_schedule(case_ids, arm_ids, repetitions, seed)`：可复现 arm 随机顺序，输出不可变 schedule。
- `run_cell(cell, policy, ledger)`：每个 cell 独立 snapshot，实际调用生产 run_case；失败也产生 outcome 记录，不只在成功时写。
- `resume_study(manifest, ledger)`：仅恢复未完成 cell，不重跑已有成功结果；in-flight 进程是否存活按 lease/owner 判断，不能并发双跑。

`ledger.py`

- `append_event(event)`：request 发送前持久化 admitted/sent，返回或异常持久化结束；单 cell append-only JSONL，event ID 防重复，关键事件 flush/fsync。
- `reconcile_requests(events)`：配对 lifecycle，挂起/usage missing 留 UNKNOWN，去重后汇总；结果写入仍保留原始事件。
- `aggregate_all_attempts(manifest)`：根据 manifest attempt IDs，而非只 glob 最后 run 目录；每个 attempt 只计一次。

`seal.py`

- `seal_cell(cell)`：保存完整 base→final diff、hash、内部状态、generation failure 的空 patch。
- `seal_study(manifest)`：检查所有预注册 cells 终态及代码 hash；输出 cohort seal。
- `evaluate_sealed(manifest)`：只有 seal 完成才允许读取官方评测数据，不能调用修复入口；infra 重试不改变 patch。

`analysis.py`

- `build_paired_outcomes`、`compute_cost_endpoints`、`paired_noninferiority`、`bootstrap_cost_ratio`、`factorial_effects`、`missing_outcome_bounds`。
- 分析脚本与 outcome 无关地冻结；输出 machine-readable JSON、每例 CSV 和论文 LaTeX table snippets 到 Paper/fse2027/generated/。不由人工修改数字。

`continuation.py`

- `sample_stop_points`、`restore_sealed_state`、`run_continuation`、`compare_suppressed_action`。
- 诊断分支独立 ledger/budget，不能覆盖主实验 final hash。主实验成本和诊断费用分列。

### 7.2 生产代码：明确政策而非一个大开关

把 policy 接入现有 ReachAvoidConfig / evidence-policy 节点，至少拆出：

| policy 字段 | 值/作用 | 必须连接到 |
| --- | --- | --- |
| recovery_schedule | fixed / on_gap | controller recovery admission |
| falsification_schedule | fixed / on_question | graph_policy challenge admission |
| suppress_same_version_actions | bool | claim_evidence_action |
| reuse_source_evidence | bool | read_evidence 与真正请求构造点 |
| compact_duplicate_payloads | bool | compact_evidence_context |
| reuse_validation_results | bool | validation_cache_key 与 queue |
| question_dependency_mode | versioned / flat_exact | question version 和 reopening |
| recovery_executor | deterministic_first / model_orchestrated | recovery 路由，二者 oracle 权限一致 |

F00/F10/F01/F11 由 protocol 显式展开成这些字段，并保存 resolved policy；不能依赖环境变量默认值。新增字段必须在事件中记录其实际触发，不允许只存在于 config。

### 7.3 事件字段及关联

共同字段：study_id、cell_id、instance_id、arm、repetition、attempt_id、event_id、event_type、timestamp、monotonic_elapsed、implementation_hash。

request event：request_id、question_id/version、action_id、stage、checkpoint_hash、provider/model/version、request_fingerprint、tool_schema_hash、usage status、input/output/cache/reasoning tokens、latency、finish/error reason。供应商不返回的 version/reasoning 数值记 absent，不捏造。

action event：normalized operation、relevant state digest、source hash、oracle hash、environment hash、admission outcome、reused evidence IDs、result observation IDs。

decision event：候选动作 IDs、选中动作、确定性 ranking reason、原/new question status/version、stop reason、final selection evidence。

不存明文 API key、sudo 密码或全量 inherited env；公开与私有 artifact 分层。保留确切 command 的私有执行记录，公开脱敏 command/template 与身份 hash。

## 8. 必须先通过的研究工具测试

- 同一 protocol/seed 产生完全相同 schedule；四臂除干预字段无差异。
- 请求失败/进程中止/重启后，request 与费用不丢不重；usage missing 不变成零。
- 模型 budget reservation 与 provider token 分开，caps 真正生效。
- 同 stage/command 但 patch 或 env 不同，不误算 state-conditioned duplicate。
- action 失败未完成与 completed-refuted 区分，暂时错误可按有限重试策略恢复。
- patch/source/oracle/env 变更触发正确失效；相同 key 不重复 admission。
- 无 target 不被认证；blocked 不 pass；NameError oracle 可匹配预期。
- 所有 case 未封存之前，harness gate 拒绝读取官方数据。
- 同 sealed patch 的 infra rerun 不改变生成成本/候选选择；generation failure 保留分母。
- 统计函数用人工可核算数据、零 resolved、缺失结果、极端长尾和模拟数据覆盖。

## 9. 执行里程碑与进入条件

M0：目录/协议/开发曝光列表整理；无需调用 API。

M1：拆开关与 crash-safe ledger、修复已有关键执行失败；单元测试 + deterministic toy，验证全部政策的路径激活。不得仅凭配置文件说支持消融。

M2：30 个开发例，先做小预算四臂；看激活、执行正确性、缺失成本，不看最终 testing cohort 调参。确认没有“一个功能关闭导致该臂实际少拿源码”等混杂。

M3：冻结版本与确认性协议，跑未见机制子集、主实验和最近邻 baseline；生成全体 seal 后统一 harness。

M4：冻结结论分析，跑 stop intervention、failure analysis、第二模型/时间 cohort 确认；不回改主实验 patch。

M5：自动生成论文表图、可复现包和 negative results。发现问题修实现后必须另开版本重跑对应实验，不混入旧 hash。

FSE 2027 的已核验官方 CFP 截止日期为 2026-10-02。应先估计本地算力/API 预算可完成的工作，不能为了截止日期隐瞒未完成实验。录用没有性能捷径：相对强基线的可信比较、机制新意、安全边界和复现性缺一不可。

## 10. 论文最终叙事对应的结果组织

- Main results：修复能力—全部成本联合表；CI、wins/losses、error 数均出现。
- Factorial ablation：D/R 独立效应与交互，不只最好的两行。
- Mechanism attribution：按阶段的调用/token；机制 coverage/hits；至少一个可复核的证据→省略动作→成本变化链。
- Safety：错误停止、stale reuse、反例漏检与局部继续执行。
- Efficiency frontier：多预算实际 cost—Resolved 曲线及图/执行开销。
- Generality：不同模型/时间 cohort 与失败分层。

只有当“非劣 + 全尝试净成本降低 + 强 baseline + 可归因机制”均有证据时，摘要才能写“preserves repair effectiveness while reducing cost”。若图的语义层没有独立贡献，就如实改写为更小的有效方法；不靠更多术语补贡献。
