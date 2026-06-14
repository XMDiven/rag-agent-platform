# rag-agent-platform

这是一个从 RAG 知识库问答逐步演进到轻量 Agent 编排的 AI 应用工程项目。

当前仓库结构把 RAG 作为 Agent 系统的第一个可运行子项目。`rag` 子项目已经支持文档摄入、批量上传、向量检索、问答生成、流式问答、来源返回、轻量问题分析、检索规划、执行 trace、Prompt 版本管理、离线评测、LLM-as-Judge 结构化评分和 Prompt A/B 对比报告。`agent` 子项目在 RAG 之上提供编排层：默认走基于 native function calling 的多步 agent loop（模型自主多轮选择并调用工具、工具结果与失败都回灌模型，由模型决定继续调工具还是收尾），并保留规则式单步编排作为降级路径；支持问题分析、工具规划、工具执行、Agent trace、摘要工具、工具失败状态返回、FastAPI `/agent/run` 接口和 `/health` 健康检查接口。

后续扩展方向是继续在当前 RAG 和轻量 Agent 基线上补齐工程证据，例如大文档处理报告、延迟 benchmark、Agent 目录结构整理、问题拆解工具和 Prompt 评测对比报告。

## 子项目

| 子项目 | 状态 | 说明 |
| --- | --- | --- |
| `rag` | RAG MVP + 轻量编排基线 | 支持 Markdown/PDF 入库、单文件/批量上传、Qdrant 检索、FastAPI 问答、流式返回、来源引用、执行 trace、Prompt 版本、离线评测、LLM-as-Judge 结构化评分和 Prompt A/B 对比报告 |
| `agent` | 多步 Agent loop 编排 | 支持工具注册、问题分析、工具规划、工具执行、摘要工具、结构化 Agent trace、工具失败处理、基于 native function calling 的多步 agent loop（自主多轮工具编排 + 规则式单步降级）、FastAPI `/agent/run` 接口和 `/health` 健康检查接口 |

## 当前能力边界

已经完成并有代码或测试支撑：

- RAG：Markdown/PDF 摄入、单文件上传、批量上传、Qdrant 索引、`/ask`、`/ask/stream`、来源引用、RAG trace、Prompt 版本、离线评估、LLM-as-Judge 评分报告和 Prompt A/B 对比报告。
- Agent：`retrieval_tool`、`summary_tool`、`question_decompose_tool`、`fallback_tool`、基于 native function calling 的多步 agent loop（`run_agent_loop`，工具失败回灌模型由其自主恢复）+ 规则式单步降级（`run_agent_once`）、`run_tool` 工具派发、executor、AgentState、Agent trace、工具失败结构化返回。

仍需要补证据后再写进简历的能力：

- 100+ 页 PDF 稳定处理。
- 平均响应时延 2s 内。
- 上下文命中率提升约 20%。
- AutoGen、网络搜索工具。
- 完整 A/B Prompt 对比平台、300+ 样本或 80+ 组实验。

## 当前运行方式

端到端演示记录见 [`docs/demo/end_to_end.md`](docs/demo/end_to_end.md)，包含
upload、ingest、ask、stream ask 和 agent run 的真实 `curl` 命令与响应摘录。

进入 RAG 子项目：

```bash
cd rag
```

然后按照 [`rag/README.md`](rag/README.md) 运行。

进入 Agent 子项目：

```bash
cd agent
conda run -n AI_DEV pytest tests/ -q
```

Agent API 入口：

```bash
cd agent
conda run -n AI_DEV uvicorn agent_app.app.main:app --reload
```

更多说明见 [`agent/README.md`](agent/README.md)。

## English

This repository is evolving from a RAG knowledge-base application into a lightweight Agent project. The [`rag`](rag/) module provides document ingestion, single and batch upload, Qdrant retrieval, FastAPI question answering, streaming responses, cited sources, lightweight query analysis, retrieval planning, execution traces, prompt versioning, and evaluation scripts. The [`agent`](agent/) module provides an orchestration layer with question analysis, tool planning, tool execution, a deterministic summary tool, Agent traces, structured tool failure handling, a multi-step agent loop built on native function calling (the model autonomously selects and calls tools across rounds, with tool results and failures fed back to it so it decides whether to keep calling tools or finish) plus a rule-based single-step fallback, a FastAPI `/agent/run` endpoint, and a `/health` endpoint.
