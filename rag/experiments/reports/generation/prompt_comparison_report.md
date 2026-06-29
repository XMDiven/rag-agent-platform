# Prompt A/B 对比报告

## 目标

本报告对比 `qa_prompt_v1` 和 `qa_prompt_v2` 在同一批 RAG golden questions 上的表现，验证 Prompt 版本变化是否带来可观测的回答质量差异。

评估使用已有的 LLM-as-Judge 流程完成。每个 case 会先走在线 RAG 问答链路生成回答，再把问题、回答和本次检索返回的 sources 交给 judge prompt，由 judge 输出结构化评分。

## 实验设置

- 测试集：11 个 golden RAG questions
- 检索配置：`RETRIEVAL_TOP_K = 7`
- 评估维度：relevance、completeness、groundedness、format
- 评分范围：1-5 分
- 通过标准：四个维度都达到 4 分及以上
- 对比报告：
  - `qa_prompt_v1`：`experiments/runs/judge/20260526-211450.json`
  - `qa_prompt_v2`：`experiments/runs/judge/20260526-212913.json`

## 结果

| Prompt | Passed | Failed | Avg relevance | Avg completeness | Avg groundedness | Avg format | Avg answer time | Avg judge time | Avg total time | Max total time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `qa_prompt_v1` | 11 | 0 | 5.00 | 4.91 | 5.00 | 4.91 | 33.80s | 39.76s | 73.56s | 107.42s |
| `qa_prompt_v2` | 11 | 0 | 5.00 | 5.00 | 5.00 | 4.91 | 39.30s | 38.90s | 78.20s | 151.01s |

## 观察结论

两个 Prompt 版本在当前 golden set 上都通过了全部 11 个 case，说明两者都能在当前检索结果基础上生成足够相关、完整且有来源支撑的回答。

`qa_prompt_v2` 的 completeness 平均分从 4.91 提升到 5.00，说明结构化提示词对回答完整性有轻微帮助。但它的平均回答生成耗时从 33.80s 增加到 39.30s，平均总耗时从 73.56s 增加到 78.20s，最大 case 总耗时也从 107.42s 增加到 151.01s。

`qa_prompt_v1` 的优势是更简单、平均回答耗时更低，适合作为默认问答 Prompt。`qa_prompt_v2` 的优势是输出结构更明确，适合需要固定回答段落或下游消费结构化内容的场景。

## 当前决策

当前默认 Prompt 继续保留 `qa_prompt_v1`。

原因：

- 两个版本在当前测试集上都达到 11/11 通过，质量差距不大。
- `qa_prompt_v1` 的平均回答生成耗时更低。
- `qa_prompt_v1` 更适合作为通用问答默认版本。
- `qa_prompt_v2` 保留为结构化输出备选版本，用于需要固定段落格式的场景。

## 当前边界

- 当前样本量只有 11 个 golden cases，不能外推到大规模 Prompt 实验。
- Judge 使用的是 RAG 流程返回的 sources，不是独立检索知识库后重新判定事实。
- 耗时受外部 LLM 服务状态、网络、输出长度和模型负载影响，不应直接写成稳定性能指标。
- 本报告能支撑“Prompt 版本化评估”和“LLM-as-Judge 结构化评分”，但不能支撑“80+ 组 Prompt 对比”或“300+ 样本评测”。

## 可支撑的简历表述

可以安全表述为：

`设计 Prompt 版本化评估流程，基于固定 golden questions 和 LLM-as-Judge 结构化评分，对不同 Prompt 版本的回答质量、groundedness 和耗时进行对比，并保存 JSON 评估报告。`
