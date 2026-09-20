# 统一证据图效率实验：20 案例阶段性快照

| 指标 | Baseline | 我们的方案 | 减少 |
| --- | ---: | ---: | ---: |
| 模型调用 | 439 次 | 323 次 | **26.4%** |
| 总 token | 6,264,375 | 4,428,109 | **29.3%** |

截止时间：2026-09-20 17:42（北京时间）。统计对象是当时已经完成生成的同一批
20 个案例，成本口径为每个案例最终保留的生成尝试，不含归档失败重试。
Baseline 官方 harness 尚未运行，49 案例实验仍在进行。
状态：`INTERIM_COST_RESULT_NOT_COMPLETED`。

这张表支持“已完成的这 20 个案例中，新方案的记录成本更低”。它尚不能证明修复
能力保持，也不代表全 cohort 或包括失败重试的总成本下降。不得将这个快照称作
最终实验成功、统计显著提升或优于 SOTA。

## 对照配置

模型为 DeepSeek Flash，temperature=0，thinking disabled；客户端未传 seed。
每例最多 160 次模型调用、1,000,000 token、3600 秒，最多 8 次 revision；
4 案例并发、4 validation workers，每例最多两次尝试。
相同生产实现 SHA-256：
`07987ce08d4462d44fd052977ae6dddd70470c4a42a26b86794c63bce458f2af`。

Baseline 保留统一图及修复器，关闭 evidence reuse 与 demand-driven interaction。
新方案开启这两个开关。本实验是效率机制消融，不是裸模型或外部 SOTA 对照。
历史方法组的重试次数不统一，部分尝试没有预算快照，环境负载亦未被严格控制。

## 发布内容

`efficiency_milestone_20260920/paired_cases.csv` 提供逐例调用数、token、patch hash、
预算 artifact hash 和控制器终态；`interim_summary.json` 提供合计与精确降幅。
`protocol.json` 保存 49 案例列表和双方配置。所有结果只含聚合指标和公开 instance ID，
不含密钥、密码、模型完整 prompt、仓库工作副本或隐藏测试内容。

`method49_official_summary.json` 保存已有方法组的官方结果：P0 为 14/49，final 为 9/49；
分别有 7 和 11 个 harness error。5 个表面下降案例的 patch 完全相同，不能归因为修复
回退。两侧都完成评测的 31 例为 9→9。baseline 结果将在完成后另行发布。

复现这个固定时间快照：

```bash
cd Code
python experiments/export_efficiency_milestone.py \
  --baseline experiments/matched_efficiency_baseline49_20260920 \
  --method experiments/evidence_efficiency_partial49_20260920 \
  --output /tmp/reachpatch-efficiency-milestone
```

相关实验入口为 `experiments/run_matched_efficiency_baseline.py`，含固定协议、
生成 seal、官方评测和全尝试成本报告；`run_evidence_efficiency.py` 与
`run_evidence_ablation.py` 为公开 toy 验证入口。

## 标签说明

此版本的 annotated tag 为 `efficiency-20cases-calls26.4pct-tokens29.3pct`，标签正文
保存上述表格及统计边界。生产算法保持与实验 hash 一致，上传期间实验继续运行。

## 本次发布验证

- 从 20 个逐例预算 artifact 重新求和，得到表格中的精确调用数和 token 数。
- `PYTHONPATH=. python -m pytest -q tests/unit/test_reachavoid_51_runner.py tests/unit/test_matched_efficiency_baseline.py`：14 passed。
- 本次运行全套 `pytest -q` 出现多项失败并在持续等待时人工中止，无完整全套结论。
- 单独复核 `test_target_recovery_agent.py -x --tb=short`：3 passed、1 failed；
  `test_probe_command_is_self_contained_for_isolated_execution` 的
  `execution_identity.project_module_files` 为空，未包含预期 `api.py`。
- 因此不声称全测试通过；为保留实验可追溯性，此快照未改动生产实现。
- 暂存内容已检查密钥/密码字面值和私钥特征，无命中；凭据文件不在提交中。
