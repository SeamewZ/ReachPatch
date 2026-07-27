# Patch-First Refactor Plan

基线：`c0cf40a0555f2713da12d797d427b5ca6c93c241`（并包含其后的 Program Graph 正确性修复）。本清单覆盖本轮所有 P0 生产链路；完成状态只在实现已接入 SWE generation 路径并通过行为测试后改为“完成”。

| 问题 | 旧函数 | 旧行为 | 新函数 | 新行为 | 验证方式 | 完成状态 |
|---|---|---|---|---|---|---|
| 初始生成晚于全量五图 | `ReachPatchController.analyze` | 语义唯一化后全量构建 Program/Requirement/Binding/Challenge，再执行 Generator | 重写 `analyze` | Semantic hypotheses → RepositoryIndex → Requirement Core → Repair Slice → 初始 Generator；diff 后才扩展 Active Graph Stack | patch-first 控制器集成测试；记录首次 patch 与各图时间 | 完成 |
| 普通语义歧义阻断 | `freeze_assignment`, `analyze` | assignment 不唯一返回 `None` 并产生 `SEMANTIC_BLOCKED` | `HypothesisSet`, `build_hypothesis_set` | 保留最多四个 coherent、authority-complete、非支配解释；提取共同 hard nodes；冲突进入 discriminator queue | 双 hypothesis 行为测试 | 完成 |
| 全仓库精细扫描 | `build_augmented_program_graph`, `DefinitionScopeAnalyzer` | 所有 Python 文件、表达式、语句和协议均物化 | `build_repository_index`, `recover_repair_slice_seeds`, `build_active_program_slice` | Index 只保存摘要与源码位置；只对局部 active callable 精细分析 | 大量无关模块性能测试；精细文件清单与节点增长断言 | 完成 |
| 图预算字段未在循环生效 | 原 builder 外层限制 | 到末尾才发现超限，可能先耗尽内存/时间 | `GraphBudget`, `Deadline`, budget checkpoints | 文件、AST、CFG、def-use、protocol、path、domain、binding、challenge 内循环提前停止并产生 soft `ANALYSIS_TRUNCATED` | 低预算测试验证部分图返回且不崩溃 | 完成 |
| AST 被长期保留/摘要过重 | builder scope caches | Repository 级分析保留大量 AST/细节点 | `RepositoryIndex` | index 仅保留 `ModuleSummary`、`SymbolLocation` 和倒排索引，解析后释放 AST | 序列化检查与 RSS benchmark | 完成 |
| 非 active callable 仍创建所有节点 | `DefinitionScopeAnalyzer.generic_visit` | 对每个表达式创建节点 | `DefinitionScopeAnalyzer(... active_callable_ids, precise)` | 只有 active callable 内创建 statement/expression；其他作用域仅摘要 | 局部图测试检查无关表达式不物化 | 完成 |
| 嵌套 callable 重复 def-use | `DefUseAnalyzer` 的 `ast.walk(callable_ast)` | 父函数再次遍历嵌套函数/类 | `iter_callable_body_without_nested_callables` | 每个 callable body 独立分析一次并跳过嵌套 callable 内部 | 嵌套函数 edge 去重测试 | 完成 |
| CFG 依赖递归 | `CFGBuilder._build_block` | 深层控制流消耗 Python 调用栈 | `build_cfg_iterative` | 显式 worklist 处理顺序、分支、循环、异常、with 与跳转 | 超递归深度 CFG 测试 | 完成 |
| Protocol IR 全量物化 | `ProtocolAnalyzer` / builder materialization | 所有候选和边均创建 | active protocol materializer | 仅 issue/diff/trace/context/high-risk active 操作物化；候选数受预算约束，其余聚合 frontier | 协议候选上限与 frontier 测试 | 完成 |
| patch 后 Program Graph 全量重建 | `evaluate_transition`, `rebuild`, controller ablation | 每轮调用 `build_augmented_program_graph` | `update_active_program_slice` | 失效 touched functions，重建 touched 与直接依赖，合并 trace edge，复用其他节点/hash | 单函数增量图测试 | 完成 |
| Requirement 首轮过度扩展 | `compile_requirement_graph` | 首轮执行 Program predicate promotion 与完整闭包 | `compile_requirement_core` | 只编译共同 hard、preferred 高可信、明确契约、visible preservation 和直接 witness 边界 | requirement core 单元/集成测试 | 完成 |
| Requirement path×partition 全乘积 | compiler 双重循环 | 无变量关联的 partition 也复制到每条 path | `join_requirement_to_paths` | trigger/observation/变量关联/可满足性约束联结，合并与支配，按 leaf 有界；其余 deferred | 无关 partition 不复制测试 | 完成 |
| 未改分支参与 domain promotion | requirement compiler/closure | 全仓库 predicate 反向提升 | `promote_domains_from_diff` | 仅 diff guard、protocol、representation、异常和状态更新驱动 promotion | `if not x` diff 产生四邻域测试 | 完成 |
| 无信息时重复 Requirement 构建 | controller/transition | 空 diff/trace 仍 compile | graph reuse gate | diff、trace、context 都空时复用 graph/hash | 空 execution 集成测试 | 完成 |
| Binding 全量物化与 closure 过强 | `build_binding_graph`, closure missing check | 所有 path obligation 都必须建 unit | `BindingStatus`, `build_active_binding_graph` | 只处理 affected/unbound；ACTIVE/CANDIDATE/DEFERRED/INFEASIBLE；active closure | 稀疏 binding 与增量复用测试 | 完成 |
| 无 Oracle 形成上千硬单元 | binding/challenge materialization | 每个无 Oracle unit 生成 hard `UNKNOWN_ORACLE` cell | aggregated `OracleFrontier` | 无 Oracle unit DEFERRED；同类聚合 frontier，不生成 Challenge Cell | 空 Challenge 测试 | 完成 |
| Challenge 无界且不排序 | `materialize_challenges` | 全量 materialize | `ChallengePriority`, `materialize_active_challenges` | 仅 ACTIVE unit；按 authority/risk/diff/info/cost 排序并限额 | 排序和上限行为测试 | 完成 |
| 空 execution 被当作 baseline 并触发重建 | `execute_challenges`, controller | 0 个真实执行仍继续 dynamic rebuild | `ChallengeExecutionResult` | 明确 real count、skipped、TraceDelta；0 次进入聚合 frontier并继续 Generator/Oracle 补充 | 空 Challenge 集成测试 | 完成 |
| Generator 核心位于实验 runner | `DeepSeekActionProvider` | runner 直接调用 API，生产控制器不可复用 | `repair/deepseek_agent.py`, `repair/tools.py`, `repair/context.py` | 生产持久 Code Agent；runner 仅装配 GenerationInstance | 生产调用链测试与 runner 审计 | 完成 |
| DeepSeek 只能选一个 AST 节点 | DeepSeek prompt/action schema | 限定 exactly one node | `GeneratorRevision`, `ProposedEdit`, tool loop | 单一机制可含多文件、多位置协调 edit；工具受控执行 | 单/多文件 edit 行为测试 | 完成 |
| 初始与反例修复会话断裂 | provider 每轮独立请求 | 不保留 inspected/context/accepted/rejected 历史 | `GeneratorConversation`, `generate_initial_patch`, `repair_from_counterexamples` | 每案例单 conversation、单 working patch lineage，commit/rollback 后历史保留 | 持久会话与单 patch 测试 | 完成 |
| 合理动作被统一转为 NO_ACTION | runner `_action_from_decision` | causal cut 未召回即丢弃 | `convert_revision_action`, `ActionConversionStatus` | active slice 接受；合法 context request 扩 slice；非法 operator/source/forbidden path 分别记录 | 五种 conversion 状态测试 | 完成 |
| Generator 无受控工具循环 | DeepSeek provider | 一次 JSON 决策，不能查代码/执行检查/补上下文 | `RepairToolExecutor`, DeepSeek tool loop | search/read/symbol/callers/references/diff/public check/slice/edit/finish；每 revision tool turns 受配置限制 | fake transport 工具调用集成测试 | 完成 |
| Repair prompt 传递错误/冗余上下文 | provider prompt | AST 节点列表，缺少真实 lineage 证据 | `build_repair_context` | 紧凑包含 issue、diff、coverage、失败、packet、divergence、slice、cut、impact、PASS、失败机制和预算 | context 快照断言 | 完成 |
| transition 不控制同一 patch | `evaluate_transition` | 单 action、全图重建，真实 revision 常未进入 | `evaluate_patch_revision` | 从 incumbent 建 trial，应用多 edit，机械/micro/历史 CE、增量图、challenge、gate、局部 rollback/commit、证书 | initial→partial→CE→repair→Reach 轨迹测试 | 完成 |
| 公开检查把 baseline 既有失败误判为 patch 回归 | transition 中单树 `mechanical_commands` | 只执行 patched tree；任意非零退出立即 Avoid，无法区分目标修复、稳定失败和 preservation 回归 | `run_public_checks_paired`, `_public_check_packet` | 每条公共检查在 incumbent/trial 成对执行并分类；只有 PASS→FAIL 回滚，FAIL→PASS 计入 Progress/Reach，FAIL→FAIL 形成真实 packet 反馈同一会话 | 四种分类参数化测试；ArtifactStore/证书断言 | 完成 |
| Avoid 把分析不完整当回滚原因 | `in_avoid_set` | UNKNOWN、图 closure、语义歧义可能阻断/回滚 | 重写 Avoid gate | 仅 apply/syntax/import/确认回归/非法修改/副作用/确认风险扩大进入 Avoid | gate truth-table 测试 | 完成 |
| Reach 要求全局 closure/全部 PASS | `in_target_set` | 低风险 UNKNOWN 永久阻止封存 | active Reach gate | 非空 patch、active targets/stable CE/confirmed preservation/diff adequacy/hashes/safe；无高价值 pending | Reach 行为测试 | 完成 |
| Progress 不能反映真实修复 | metrics | 依赖粗粒度 frontier/count | `progress_metrics` | 目标 PASS、稳定失败/CE 消除、diff adequacy、preservation 回归、高风险 unknown、impact delta | commit/rollback 测试 | 完成 |
| 纯 UNKNOWN 立即接受或回滚 | transition | 无 targeted expansion 中间态 | transition analysis outcome | 保留 trial context，执行一次 targeted challenge/slice expansion后再决策 | UNKNOWN transition 测试 | 完成 |
| 恢复时全量重建 | `ReachPatchController.rebuild` | 从源码重新构造所有图 | artifact-backed active restore | 优先加载 ArtifactStore 图，只核验 active source/hash；缺失部分才局部恢复 | resume hash/reuse 测试 | 完成 |
| ablation 灾难性双全图 | controller ablation | current/candidate 每 edit group 各建全图 | `enable_ablation=False`, incremental affected-slice ablation | 默认关闭；仅 Reach 且预算足够时对 affected slice 验证 | 默认不调用全图 builder 测试 | 完成 |
| Harness/gold 信息可能进入 generation 模型 | SWE runner instance/result plumbing | generation 与 harness 数据边界未类型隔离 | `GenerationInstance`, `HarnessEvaluationInstance` | Generator 只能接收公共 evidence；sealed patch 后独立 harness 目录，ArtifactStore/resume 不读取结果 | leakage 字段与路径审计测试 | 完成 |
| 配置上限只定义不消费 | `ReachPatchConfig` | 原流程没有所列 active 图/agent限额 | 扩展 config 并贯穿 index/slice/path/bind/challenge/tool loops | 每项配置在生产循环参与停止/选择并产生指标/frontier | 极低上限端到端测试 | 完成 |
| Controller 阶段不能表示 patch-first 路径 | `ControllerPhase` | 阶段围绕全量分析 | 扩展 phase enum 与合法转移 | `SEMANTIC→INDEX→INITIAL_LOCALIZATION→INITIAL_GENERATION→MECHANICAL_VALIDATE→ACTIVE_GRAPH_BUILD→CHALLENGE_EXECUTE→TRANSITION_GATE`，失败进入反馈修复 | 状态机序列测试 | 完成 |
| 指标不足，无法验收真实效果 | runner/reporting | 缺少 patch-first 与 active graph 指标 | production metrics + SWE report | 记录首次 patch、Index/Slice、图规模、RSS、leaf/binding/challenge/execution、tool/revision、commit/rollback、patch 非空 | 集成运行报告断言 | 完成 |
| 被替代旧生产路径仍可达 | controller/transition/recovery/runner | 新旧实现可能并存且旧路由仍被调用 | 调用链清理与兼容 facade | CLI/SWE generation 只走 patch-first；旧全图 API 仅显式离线 analyze 可用 | `rg` 审计 + monkeypatch fail-if-called 测试 | 完成 |
| 最终实现与隔离证据不完整 | 现有报告 | 未覆盖本轮 patch-first 改造 | 四份本轮报告 | 逐项记录旧/新函数、算法、调用链、性能、测试、残余问题及十个明确问题答案 | 报告内容与测试结果复核 | 完成 |

## 实施顺序

1. Semantic hypotheses、配置、阶段、RepositoryIndex 与 GraphBudget。
2. Repair Slice、局部 Program Graph、迭代 CFG、def-use/protocol 预算与增量更新。
3. Requirement Core、约束联结、diff promotion；稀疏 Binding 与有界 Challenge。
4. 生产 DeepSeek 持久工具会话、多编辑 revision 与 action conversion。
5. Patch-first Controller、`evaluate_patch_revision`、Reach/Avoid/Progress、Artifact 恢复。
6. Generation/Harness 数据隔离、SWE runner 接线、指标与报告。
7. 六类行为测试、性能基准、生产调用链与禁止项审计。
