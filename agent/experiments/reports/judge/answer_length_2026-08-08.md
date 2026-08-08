# 压缩答案长度的成本与质量取舍（2026-08-08）

## 结论

在 `AGENT_LOOP_SYSTEM_PROMPT` 增加四条精简约束后，retrieval 类 7 个 case：

| 指标 | before | after | 变化 |
|---|---:|---:|---:|
| 平均输出 token | 1054 | 689 | **−35%** |
| 平均输入 token | 5001 | 3694 | −26% |
| 平均 LLM 调用次数 | 4.6 | 3.7 | −20% |
| 平均 Agent 耗时 | 23.9s | 14.7s | **−38%** |

三条事前判据（输出降 ≥30%、耗时同向下降、retrieval 通过率不低于 before）全部达成。

**更重要的发现：答案越长，groundedness 越差。** 这不是附带效果，而是本轮最有价值的量化结果，见下文。

## 事前判据

在跑 after 之前设定，未事后调整：

- 平均输出 token 降低 ≥30%
- Agent 平均耗时降低，方向与 token 一致
- retrieval 类通过率不低于 before

## 改动

只改一处，`agent/src/agent_app/prompts/agent_loop.py` 追加：

```
Keep the final answer compact: lead with the direct answer in one or two
sentences, then at most five supporting points. Do not restate the
question, do not make the same point twice in both prose and a table,
and do not add sections the question did not ask for.
```

四条约束分别针对 before 数据里观察到的膨胀来源：层级小标题堆叠、开头复述问题、正文与表格重复同一论点、自作主张增加「选型建议」等未被问及的章节。

**没有使用 `max_tokens` 截断**：那会从句子中间切断输出，省了 token 但毁掉可用性，且会拉低格式与完整性评分。也**没有禁止结构化输出**——格式分一直是满分 5，禁表格反而可能掉分。约束的是重复与扩写，不是排版。

## 逐 case 输出变化

| Case | before | after | 变化 |
|---|---:|---:|---:|
| `paged_attention` | 403 | 172 | −57% |
| `chroma_vs_qdrant` | 1825 | 875 | −52% |
| `agent_framework_multi_source` | 2397 | 1192 | −50% |
| `summary_literal_text` | 146 | 84 | −42% |
| `direct_greeting` | 15 | 9 | −40% |
| `summary_longer_literal_text` | 149 | 102 | −32% |
| `qdrant_purpose` | 530 | 362 | −32% |
| `rag_definition` | 623 | 430 | −31% |
| `react_pattern` | 630 | 458 | −27% |
| `langchain_vs_llamaindex` | 972 | 866 | −11% |

原本最长最贵的三个降幅最大。输入 token 同步下降 26% 是连带效果：答案变短后，回灌给下一轮的上下文也变短，多轮之间会累积。

## 质量：单次出现互换，重复运行后澄清

单次 after 运行的整体通过率与 before 相同（8/10，retrieval 5/7），但构成发生互换：

- `agent_framework_multi_source`：❌ → ✅
- `langchain_vs_llamaindex`：✅ → ❌

按本项目既有规则，单次通过率是噪声，互换不能解读为质量变化。对这两个 case 各重复运行 5 次：

| Case | 改动前历史 | 本轮 5 次 |
|---|---|---|
| `langchain_vs_llamaindex` | 单次失败 | **5/5 通过**，groundedness 5 次均为 4 |
| `agent_framework_multi_source` | 0/5（2026-08-03 两轮）、1/5（引用修复后） | **2/5 通过**，groundedness 3–4 |

`langchain_vs_llamaindex` 的单次失败确认为抽样噪声。`agent_framework_multi_source` 从长期 0/5 提升到 2/5，是这个 case 首次出现稳定的通过样本。

## 核心发现：答案长度与 groundedness 负相关

`agent_framework_multi_source` 的 5 次重复运行中，同一份代码、同一个问题，输出长度差异很大，且与结果一致相关：

| 结果 | 输出 token | 平均 |
|---|---|---:|
| 通过（2 次） | 1804、1422 | **1613** |
| 失败（3 次） | 2208、1769、2164 | **2047** |

before 基线也指向同一结论：7 个 retrieval case 中最长的两个（1825、2397 token）恰好是仅有的两个失败，而最短的 403 token 那个通过。

**机制是合理的：模型写得越多，越会超出证据去补充内容，groundedness 就越低。** 所以压缩答案不只是省成本，它同时是一种防幻觉手段。

这也解释了为什么本轮 `agent_framework_multi_source` 能从 0/5 变成 2/5——它的输出从 2397 降到 1400–2200 区间，落进了更容易及格的长度带。但它仍是全部 case 中最长的，也仍是唯一不稳定的，两者一致。

## 数字口径说明

**after 的平均输出用 689 而不是 622。**

单次 after 运行算出的 retrieval 平均输出是 622 token（−41%）。但重复运行显示，`agent_framework_multi_source` 的输出在 1192–2208 之间波动，单次那一发（1192）落在偏短的一端。用它的 5 次均值（1873）替换单次值，`langchain_vs_llamaindex` 同样用 5 次均值（655）替换，重算得到 689 token，降幅 **−35%**。

两个数都真实，但 689 采样更充分。**报告以更保守的 −35% 为准**，避免用一次好运气夸大结果。

## 边界

- 除 `agent_framework_multi_source` 与 `langchain_vs_llamaindex` 外，其余 case 各只跑了一次；它们的输出长度方差未测量。
- 耗时 −38% 来自单次运行，且与模型当时的负载有关，不应外推为固定收益。
- Judge 与 Agent 同模型，存在自评偏差。
- 未改 `max_tokens`、判分阈值、数据集或检索配置；本轮唯一变量是 Agent 收尾提示词。
- 「长度与 groundedness 负相关」目前建立在 5 次重复 + 7 个 case 的横向观察上，样本量小，只能作为工作假设，不足以作为定量规律。

## 下一步

1. 该结论可以直接用于 `chroma_vs_qdrant`：它仍输出 875 token 且长期失败，但根因是知识库缺少 Chroma 语料，压缩长度不解决证据缺失。
2. 若要进一步验证「长度—质量」关系，正确做法是对同一个 case 用不同长度约束各跑 N 次，画出通过率随长度的变化，而不是继续在混合数据里找相关性。
3. 成本口径已可计算：输出降 35%、输入降 26%，配上单价即可换算成金额（当前单价配置默认为 0）。
