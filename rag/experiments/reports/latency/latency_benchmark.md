# RAG 问答延迟 Benchmark

## 测试环境

- 基线测试日期：2026-05-23（`kimi-k2.5`）
- 优化复测日期：2026-06-29（`kimi-k2.6` + `thinking=disabled`）
- 运行环境：本地开发环境
- 向量数据库：Qdrant
- 默认运行命令：`python -m rag_app.scripts.benchmark_latency`
- Top-K 对比命令：`RETRIEVAL_TOP_K=3 python -m rag_app.scripts.benchmark_latency`
- 测量范围：完整 `ask_question()` 在线问答链路

## 测试方法

本 benchmark 使用固定问题集调用 RAG 在线问答流程。

耗时统计包含：

- 问题分析
- 检索规划
- 向量检索
- 上下文格式化
- LLM 回答生成

其中 `retrieval_duration_seconds` 来自 `retrieval` trace，`generation_duration_seconds` 来自 `generate_answer` trace，`total_duration_seconds` 是 benchmark 脚本外层统计的完整调用耗时。

## 测试结果

### 优化结果（当前默认）：kimi-k2.6 + thinking=disabled

基线（`kimi-k2.5`）定位到瓶颈在 LLM 生成阶段后，将默认问答链路切换为 `kimi-k2.6` 并关闭 thinking（`LLM_THINKING_TYPE=disabled`），其余变量冻结（同一 3-case 集、`top_k=7`、同索引）。所有 case 的 `generation_status` 均为 `completed`、`answer_is_fallback` 均为 `false`。

代表性单次运行（warm，第 7 次）：

| case_id | total_duration_seconds | retrieval_duration_seconds | generation_duration_seconds |
|---|---:|---:|---:|
| rag_definition | 3.98 | 0.33 | 3.65 |
| qdrant_usage | 6.65 | 0.34 | 6.31 |
| langchain_usage | 7.22 | 0.34 | 6.88 |

汇总：

- 测试问题数：3
- Top-K：7
- 平均耗时：5.95 秒
- 最大耗时：7.22 秒
- 最小耗时：3.98 秒

稳定性（连续 7 次运行，LLM 生成耗时本身有波动）：

- 各次平均耗时区间：5.75 ~ 6.42 秒
- 7 次总平均：≈ 5.9 秒
- 平均耗时跌破 5.75 秒的次数：0
- 检索耗时（warm）稳定在 ~0.35 秒，耗时几乎全部在 LLM 生成

**前后对比**：固定 3-case benchmark 平均耗时由基线 `kimi-k2.5` 的 24.43 秒降至约 5.9 秒，约 4 倍提升。瓶颈位置不变（仍在生成阶段），优化来自模型切换 + 关闭 thinking，而非检索侧改动。

### 历史基线：kimi-k2.5

本次有效 benchmark 使用 `kimi-k2.5`，所有 case 的 `generation_status` 均为 `completed`，`answer_is_fallback` 均为 `false`。

| case_id | total_duration_seconds | retrieval_duration_seconds | generation_duration_seconds | generation_status | answer_is_fallback |
|---|---:|---:|---:|---|---|
| rag_definition | 16.52 | 0.24 | 16.27 | completed | false |
| qdrant_usage | 32.62 | 0.24 | 32.38 | completed | false |
| langchain_usage | 24.16 | 0.28 | 23.87 | completed | false |

汇总：

- 测试问题数：3
- Top-K：7
- 平均耗时：24.43 秒
- 最大耗时：32.62 秒
- 最小耗时：16.52 秒

### 历史 Top-K 对比：top_k = 7

| case_id | total_duration_seconds | retrieval_duration_seconds | generation_duration_seconds |
|---|---:|---:|---:|
| rag_definition | 23.03 | 1.15 | 21.87 |
| qdrant_usage | 29.08 | 0.24 | 28.83 |
| langchain_usage | 26.29 | 0.28 | 26.00 |

汇总：

- 测试问题数：3
- Top-K：7
- 平均耗时：26.13 秒
- 最大耗时：29.08 秒
- 最小耗时：23.03 秒

### 历史 Top-K 对比：top_k = 3

| case_id | total_duration_seconds | retrieval_duration_seconds | generation_duration_seconds |
|---|---:|---:|---:|
| rag_definition | 34.66 | 1.98 | 32.67 |
| qdrant_usage | 18.40 | 0.24 | 18.15 |
| langchain_usage | 22.08 | 0.55 | 21.52 |

汇总：

- 测试问题数：3
- Top-K：3
- 平均耗时：25.05 秒
- 最大耗时：34.66 秒
- 最小耗时：18.40 秒

## 结论

分段耗时统计显示主要耗时来自 LLM 生成阶段，而不是向量检索阶段：检索在所有样本中均低于 1 秒（warm 时 ~0.35 秒），瓶颈完全集中在生成。

先验证了检索侧变量：将 Top-K 从 7 降到 3 没有带来稳定、明确的延迟优化（平均从 26.13 秒降到 25.05 秒，但 `rag_definition` 反而从 23.03 秒升到 34.66 秒），印证了"优化不应继续调检索参数，而应针对生成侧"。

据此对生成侧下手：将默认模型从 `kimi-k2.5` 切换为 `kimi-k2.6` 并关闭 thinking，固定 3-case benchmark 平均耗时由 24.43 秒降至约 5.9 秒，约 4 倍提升，且瓶颈位置（生成阶段）保持不变。

后续仍可继续压缩生成耗时的方向：

- 更短的 Prompt
- 更严格的输出长度限制
- 更小的上下文 token budget
- 流式输出以降低首字时延（TTFT）感知

## 简历表述边界

当前可以安全表述为：

`设计 RAG latency benchmark，对完整问答链路分段耗时统计，定位瓶颈在 LLM 生成阶段；通过切换 kimi-k2.6 并关闭 thinking，固定 3-case benchmark 平均耗时由 24.43s 降至约 5.9s（约 4 倍）。`

当前不应表述为：

- `平均响应时延控制在 2 秒内。`
- `平均耗时降至 4.19s。`（任何一次运行的平均都未跌破 5.75s，该数字不可复现）
