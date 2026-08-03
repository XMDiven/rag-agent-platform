# Agent 答案质量 Judge · 任务分型后的复评（2026-08-03）

## 结论

把 12 个 case 按任务类型分型、并为非检索类任务定义独立判分口径后，可判分的 10 个 case 通过 9 个（90%），基线为 12 个 case 通过 5 个（41.7%）。

**这个提升不能整体归因为答案质量变好。** 其中一部分是评测口径修正（可复现、可解释），另一部分是 Judge 与 Agent 的运行波动（同一份代码两次运行结果不同）。本报告把两者分开列出，不合并成一个「提升 48 个百分点」的说法。

## 改了什么

数据集 `agent/experiments/datasets/agent_eval_cases.json` 为每个 case 增加必填字段 `task_type`：

| 类型 | 数量 | 判分口径 |
|---|---:|---|
| `retrieval` | 7 | 沿用原 RAG rubric，四项分数均需 ≥4 |
| `summary` | 2 | 证据换成用户在提问里给出的原文，而不是检索结果 |
| `direct` | 1 | 不评 groundedness，只看相关性、完整性、格式 |
| `input_validation` | 2 | 不做答案质量评分，从通过率分母中剔除 |

代码改动在 `agent/src/agent_app/scripts/evaluate_agent_answers.py`：`build_judge_sources` 负责 summary 的证据替换，`scored_dimensions` / `judge_passes` 负责按类型选择判分维度，`summarize_results` 的通过率只统计 `judge_applicable=True` 的 case 并给出分类型明细。

**未修改** Judge 的 4 分阈值、Agent 的提示词、工具实现或知识库内容。

## 分型带来的确定性变化

这三项与运行波动无关，重跑必然一致：

- `empty_question`、`whitespace_question`：原本被要求「答案要有检索证据支撑」，而它们验证的是空输入降级，结构上不可能通过。现在不纳入答案质量统计，分母从 12 变 10。
- `summary_literal_text`、`summary_longer_literal_text`：证据改为用户原文后，两次运行均为 5/5/5/5。基线时它们因 `No retrieved evidence` 被压低 groundedness 而失败。
- `direct_greeting`：不再评 groundedness。这一项恰好被本次实验证实是必要的——见下节。

## 运行波动：同一份代码，两次结果不同

分型后跑了两轮，中间只加了 `direct` 的判分口径，Agent 侧代码与提示词完全没动：

| Case | 运行 A `20260803-090720` | 运行 B `20260803-091609` |
|---|---|---|
| `direct_greeting` | grd=3 **失败** | 不评 grd **通过** |
| `paged_attention` | grd=3 **失败** | grd=4 **通过** |
| `chroma_vs_qdrant` | 2/2/2 **失败** | 5/5/4 **通过** |
| `agent_framework_multi_source` | grd=3 **失败** | grd=3 **失败** |
| retrieval 类通过率 | 4/7（57.1%） | 6/7（85.7%） |

`chroma_vs_qdrant` 从相关性 2 分跳到 5 分，`paged_attention` 的 groundedness 从 3 到 4——**这两个 case 在基线报告里被判定为「真实答案质量问题」，但没有任何修复动作它们就通过了**。

`direct_greeting` 的表现最能说明问题：基线通过、运行 A 失败，两次都用同一套 rubric、同一个问题。它证明「拿 groundedness 去评一个打招呼」产出的是噪声而不是信号，这正是给它单独定口径的理由。

## 本轮汇总（运行 B）

| 指标 | 结果 |
|---|---:|
| 可判分 case | 10（另 2 个 `input_validation` 跳过） |
| 通过率 | 9/10（90%） |
| 分类型通过率 | retrieval 6/7、summary 2/2、direct 1/1 |
| 平均相关性 | 5.0 / 5 |
| 平均完整性 | 4.7 / 5 |
| 平均 groundedness | 4.3 / 5 |
| 平均格式 | 5.0 / 5 |
| 平均 Agent 耗时 | 29.843 秒 |
| P95 总耗时 | 128.028 秒 |

## 唯一稳定失败的 case

`agent_framework_multi_source` 两轮均为 groundedness=3。Judge 指出的问题是引用错配与超出证据的推断，例如把证据里的 "supporting the OpenAI Responses API" 读成 "provider-agnostic, supporting 100+ other LLMs"。这是三个 case 里唯一在重复运行下稳定复现的 groundedness 缺陷。

## 证据边界

- Judge 与 Agent 仍是同一个模型（`kimi-k2.6`），存在自评偏差，本报告只作内部回归信号。
- 每个 case 只跑一次，样本量为 1；上面的波动证据说明**单次通过率不足以判断答案质量是否改善**。
- 未调整 4 分阈值，未删改任何 case，未修改 Agent 提示词。
- 原始 JSON 位于被 Git 忽略的 `agent/experiments/runs/judge/`。

## 下一步

不要基于 90% 这个数字做任何优化决策。先解决样本量问题：对 3 个多步 retrieval case（`chroma_vs_qdrant`、`agent_framework_multi_source`、`paged_attention`）各重复运行 5 次，得到带分母的 groundedness 失败率与分数分布，再决定是否需要改提示词——以及改完之后如何判断「真的改好了」而不是又一次抽样运气。
