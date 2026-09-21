# 2026-09-21 实验续跑记录

整体状态：NOT COMPLETED。

开发轮 `evidence_study_dev30_20260920_r1` 在14/120终态、16次attempt启动后中止。
其中4次生成失败具有相同根因：修复工具处理补丁上下文不匹配时引用未定义的 `cls`。
已修正为实例上的 `_compact`，并修正非法工具JSON参数导致的二次异常。

另外，模型工具循环原来无条件启用重复抑制，使关闭复用的消融臂仍含此机制。
已把该入口接入 `suppress_same_version_actions`，用启用/关闭两侧的实际工具行为测试验证。

新增6项回归测试通过：上下文不匹配、数组/null/非法JSON，以及工具重复抑制两侧。
测试实际经过生产controller，验证失败编辑后仍可刷新源码、提交正确补丁并REACHED；
三次模型调用全部计费，不把失败编辑算作免费。

旧轮已记录189次模型请求、2,152,814 provider tokens；失败和中止费用保留，
不得混入或剔除后冒充新轮同条件实验。旧轮未启动官方评测。

新版本 `evidence_study_dev30_20260921_r2` 已启动，沿用30个案例、四臂、随机顺序及40-call/250k/900秒预算，从头独立生成。
另补阶段调用/token/time分解、成本和时延分位数、按issue bootstrap的因子效应与交互。
这些属于探索性开发分析，不是非劣检验，不预设显著性或正向结果。

冻结版本完整生产测试：283 passed，165 legacy tests deselected，耗时314.77秒。
命令：`python -m pytest -v -o faulthandler_timeout=90 --junitxml=experiments/evidence_study_frozen_tests_20260921_r2.xml`。
报告保存在 `Code/experiments/evidence_study_frozen_tests_20260921_r2.xml`，完整日志同名 `.log`。

新轮冻结实现 hash：`7ce21b66fee1c83d45b448cb164680482ad29bf6ea6f62bf3c2ed4cc9b0b522b`。
协调器命令：`python -m experiments.evidence_study.run run --root experiments/evidence_study_dev30_20260921_r2 --key-path /home/slt/ReachPatch/ds_pwd.txt --evaluate`。
启动确认：两个独立cell已有执行日志；启动检查时已收到2次模型响应，共16,133 provider tokens，未发现缺失usage。这是运行中快照，不是最终成本。
所有120个cell封存后，协调器才允许统一官方评测、结果导出与论文编译。
运行中不修改冻结的生产代码或实验驱动；论文文字和执行记录不改变实验条件。

本轮尚不能回答官方Resolved@1、能力非劣、显著性或各机制独立收益。
外部baseline、未见数据、细粒度消融及停止后继续干预仍需后续独立实验。

论文方法段已同步当前动作生命周期：特定可重试错误最多允许两次总尝试，而非旧版的无条件至多一次。
PDF重新编译成功；日志未发现undefined reference或Overfull，仍有字体请求及Underfull排版警告。

## 12:31 CST 恢复记录

协调器在52/120终态后停止，最后请求记录停在04:55 CST附近；确切退出原因暂无证据。
此前将两个未终态单元描述为“正在运行”不准确：续跑前宿主进程检查未找到实验进程，协调器锁亦为空闲。
实现hash再次校验一致，不修改冻结代码、预算、顺序或模型。

按每单元一次attempt协议，`b012-r0-F00`、`b013-r0-F00`保留请求账本并记录中断失败，返回码125；不免费补跑或删除失败成本。
恢复后54/120终态（41生成成功、13生成失败），`b013-r0-F01`与`b014-r0-F01`已开始。
两个中断单元属于运行基础设施中断，论文分析须与算法生成失败区分，并报告对组间比较的影响。

续跑现由独立用户级systemd服务 `reachpatch-evidence-dev30-r2.service` 执行，
设置 `Restart=on-failure`、`RestartSec=30s`，不再依附对话终端。
启动后核验为active/running，协调器锁被持有，未发生服务重启。
服务日志通过 `journalctl --user-unit=reachpatch-evidence-dev30-r2.service` 查看；各cell仍保存原始日志。
所有单元封存后自动官方评测及论文导出。服务独立于对话，但不承诺主机重启等基础设施故障下绝不中断。

## 2026-09-21 完成记录

新版开发轮已完成生成、封存和官方 harness：120/120 cells，74 个生成成功、46 个生成失败。
四组官方结果（30 个问题/组）为：F00 7/30，F10 8/30，F01 8/30，F11 8/30。
四组 harness 均无 error，所有问题均有 resolved/unresolved 终态；这些是本轮开发队列的确定结果。

请求账本仍有 provider usage 缺失：F00 7、F10 8、F01 8、F11 7 个请求。
已报告 token 下界分别为 3,419,018、2,702,283、3,571,680、2,961,716；
由于缺失 usage，协议禁止计算完整成本降幅或显著性。调用数分别为323、270、334、282。

发现 harness 版本将 JSON 报告写到 `Code/` 根目录而非 `--report_dir`；日志已证明四组均成功完成。
已增加只读 reconciler，按 sealed prediction hash 找回四份不可变报告，没有重跑容器或模型调用。
论文表格已更新为实际结果，仍明确标注成本为下界、开发集且不能支持非劣或因果收益声明。

本轮实验流程完成，但 held-out 主实验、完整 usage 成本比较、独立机制消融、外部 baseline 和统计非劣验证仍未完成；总体研究状态仍为 `NOT COMPLETED`。
