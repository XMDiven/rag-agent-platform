# LLM-as-Judge Evaluation Report

## 测试目标

本实验验证 RAG 回答质量评测链路是否能通过 LLM-as-Judge 输出结构化评分结果。

评测输入包括：

- 用户问题
- RAG 生成的回答
- 本次回答返回的 `sources` 证据片段

Judge 只基于提供的 retrieved evidence 评分，不使用外部知识。

## 评测维度

Judge 输出由 Pydantic schema 校验，分数范围为 1 到 5：

| 字段 | 含义 |
| --- | --- |
| `relevance_score` | 回答是否直接回应问题 |
| `completeness_score` | 回答是否覆盖关键点 |
| `groundedness_score` | 回答是否被 retrieved evidence 支撑 |
| `format_score` | 回答是否清晰、格式是否稳定 |
| `overall_pass` | 只有所有分数都不低于 4 时为 true |
| `feedback` | Judge 给出的文字反馈 |

## 运行命令

```bash
cd rag
conda run --no-capture-output -n AI_DEV python -m rag_app.scripts.evaluate_answers_with_judge
```

运行前需要已有 Qdrant 索引，并且 LLM 环境变量可用。

## 代表性运行结果

报告文件：

```text
rag/experiments/runs/judge/20260526-162728.json
```

汇总结果：

| 指标 | 结果 |
| --- | ---: |
| prompt_version | qa_prompt_v1 |
| total cases | 11 |
| passed | 11 |
| failed | 0 |

所有 case 的 Judge 分数均为：

| score | value |
| --- | ---: |
| relevance_score | 5 |
| completeness_score | 5 |
| groundedness_score | 5 |
| format_score | 5 |

## 结论

本次运行证明当前项目已经具备最小 LLM-as-Judge 评测闭环：

```text
ask_question -> answer/sources -> LLM judge -> Pydantic schema -> JSON report
```

这可以支撑以下项目表述：

```text
引入 LLM-as-Judge 评分链路，使用 Pydantic 定义相关性、完整性、事实支撑性和格式规范性评分结果，并将评测结果保存为结构化报告。
```

## 当前边界

当前 Judge 使用的是 RAG 本次回答返回的 `sources`，用于评估回答是否被本次 retrieved evidence 支撑。

它还不是独立参考检索式 Judge，暂不评估“知识库中是否存在更完整的参考证据”。后续如果要增强评测可信度，可以增加独立 judge retriever，并将 `answer_sources` 和 `judge_sources` 分开记录。
