# Agent 提示词加固 groundedness 的前后对比（2026-08-03）

## 结论

给 `AGENT_LOOP_SYSTEM_PROMPT` 增加三条忠实性约束后，重跑同样的 3 个 case × 5 次。

**目标 case `agent_framework_multi_source` 仍然 0/5，未达到事前设定的 ≥2/5 标准。本次修改视为未达标。**

另两个 case 有小幅上移且无回归，但样本量为 5，不足以称为改善。

更重要的是：复测的 Judge 反馈定位到了真正的原因——**引用编号错位是一个结构性缺陷，不是模型「过度发挥」**，提示词无法修复它。详见「根因」一节。

## 事前判定标准

在跑之前设定，未事后调整：

- `agent_framework_multi_source`：0/5 → 至少 2/5 才算有效。
- `paged_attention`：不得低于 2/5，否则视为回归。
- `chroma_vs_qdrant`：预期仍为 0/5（根因是语料缺失）。

## 前后对比

| Case | 改前通过 | 改后通过 | 改前 grd 均值 | 改后 grd 均值 | 改后 grd 区间 |
|---|---:|---:|---:|---:|---|
| `agent_framework_multi_source` | 0/5 | **0/5** | 2.8 | 3.0 | 3–3 |
| `chroma_vs_qdrant` | 0/5 | 1/5 | 2.4 | 3.0 | 2–4 |
| `paged_attention` | 2/5 | 3/5 | 3.4 | 4.2 | 4–5 |
| 合计 | 2/15 | 4/15 | — | — | — |

目标 case 的 groundedness 从「2–3 抖动」收敛成「5 次全是 3」，方差变小但从未触及 4 分线。这说明 Judge 每次都能找到同一类问题，不是随机扣分。

`paged_attention` 的 groundedness 最小值从 3 升到 4，是三者中变化最明显的；但 5 次样本无法排除运气。

## 根因：引用编号错位

复测的 5 次 Judge 反馈都指向同一件事，例如：

> The claim that LangGraph supports 'Human-in-the-loop' [5] cites 'langchain-docs.md' but the provided evidence [21] only states that 'LangChain agents are built on top of LangGraph'

这不是模型编造内容，而是**引用标记指向了错误的证据条目**。机制如下：

1. `retrieval_tool` 每次调用返回一段带 `[1]`–`[7]` 引用标记的 RAG 答案，编号只在这一次调用的 7 条来源内有效。
2. 该 case 触发 4 次检索，Agent 收到 4 份各自从 `[1]` 开始编号的答案。
3. `collect_tool_sources` 把 4 次的来源拼成一个 28 条的列表（`agent/src/agent_app/orchestration/loop.py:65`）。
4. Agent 合成最终答案时沿用了各段原有的引用标记，但这些标记无法在 28 条的聚合列表里被正确解析。

于是 Judge 拿聚合后的 28 条证据去核对答案里的 `[5]`，必然对不上。**这是编号作用域的设计缺陷，任何提示词都无法修复**——因为在当前数据结构下，`[5]` 本身就是有歧义的。

单次检索的 case（如 `rag_definition`）不受影响，因为只有一份编号。这也解释了为什么只有多步 case 稳定失败。

## 处置

保留本次提示词修改，但**不计为成绩**：

- 保留的理由：三个 case 无一回归，groundedness 均值同向上移，且这三条约束本身语义正确。
- 不计为成绩的理由：主目标未达标，其余变化在 5 次样本下无法与噪声区分。

## 下一步

修引用编号，而不是继续调提示词。可选方案：

1. **聚合时重编号**：Agent 汇总多次检索结果时，把各段的 `[i]` 重写成聚合列表中的全局序号。改动集中在 `compact_tool_payload` 与来源聚合处，语义最正确，但需要解析并改写答案文本。
2. **回灌时去掉编号**：把工具答案里的引用标记剥离，只保留来源文件名清单，让模型在最终答案里按聚合列表重新引用。实现简单，代价是丢失段落级对应关系。

无论选哪个，验收方式相同：`--repeat 5` 复测 `agent_framework_multi_source`，看 0/5 是否变化。

## 证据边界

- 每个 case 5 次，样本量小，比例的置信区间很宽。
- Judge 与 Agent 同模型，存在自评偏差。
- 前后两轮之间只改动了 `agent/src/agent_app/prompts/agent_loop.py`，未动阈值、数据集或工具实现。
- 原始 JSON：改前 `20260803-102133.json`、改后 `20260803-120642.json`，均被 Git 忽略。
