# 端到端 Demo

本文件记录 RAG 与 Agent 两个子项目在本地真实跑通一次的命令与响应，供复现与验证。
所有命令与输出均为真实运行结果，照着重跑可得到同形状的响应。

## 运行元信息

- 运行时间：2026-06-14 CST
- 仓库：`/Users/mdiven/Code/Projects/rag-agent-platform`
- Conda 环境：`AI_DEV`
- RAG 服务：`http://127.0.0.1:8001`
- Agent 服务：`http://127.0.0.1:8002`
- LLM：Kimi（OpenAI 兼容接口）
- 向量库：Qdrant，collection `documents`
- 入库示例文件：`rag/data/raw/qdrant-docs.md`

端口约定：Agent API 用 `8002`，RAG API 用 `8001`。

## 前置条件

运行以下命令前，live stack 必须就绪：

```bash
cd /Users/mdiven/Code/Projects/rag-agent-platform/rag
docker compose up -d qdrant
conda run -n AI_DEV python -m rag_app.scripts.reset_index
conda run -n AI_DEV python -m rag_app.scripts.build_index
```

启动 RAG 服务：

```bash
cd /Users/mdiven/Code/Projects/rag-agent-platform/rag
conda run -n AI_DEV uvicorn rag_app.app.main:app --host 127.0.0.1 --port 8001
```

启动 Agent 服务：

```bash
cd /Users/mdiven/Code/Projects/rag-agent-platform/agent
PYTHONPATH=/Users/mdiven/Code/Projects/rag-agent-platform/agent/src:/Users/mdiven/Code/Projects/rag-agent-platform/rag/src \
  conda run -n AI_DEV uvicorn agent_app.app.main:app --host 127.0.0.1 --port 8002
```

健康检查：

```bash
curl -sS http://127.0.0.1:8001/health
curl -sS http://127.0.0.1:8002/health
```

实测响应：

```json
{"status":"ok"}
```

```json
{"status":"ok","service":"agent"}
```

## 1. 上传文档

命令：

```bash
cd /Users/mdiven/Code/Projects/rag-agent-platform
curl -sS -X POST http://127.0.0.1:8001/documents/upload \
  -F "file=@rag/data/raw/qdrant-docs.md;type=text/markdown"
```

实测响应：

```json
{
  "filename": "qdrant-docs.md",
  "saved_path": "qdrant-docs.md",
  "content_type": "text/markdown"
}
```

## 2. 文档入库

命令：

```bash
curl -sS -X POST http://127.0.0.1:8001/documents/ingest \
  -H "Content-Type: application/json" \
  -d '{"filename":"qdrant-docs.md"}'
```

实测响应：

```json
{
  "path": "/Users/mdiven/Code/Projects/rag-agent-platform/rag/data/raw/qdrant-docs.md",
  "document_count": 1,
  "chunk_count": 1,
  "stored_count": 1
}
```

## 3. RAG 问答

命令：

```bash
curl -sS -X POST http://127.0.0.1:8001/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"Qdrant 在向量检索中有什么用途？"}'
```

实测响应（摘录）：

```json
{
  "answer": "Qdrant 在向量检索中用作向量相似性搜索引擎和向量数据库，用于存储、搜索和管理带有附加载荷的向量数据，支持语义相似性匹配、混合检索、基于元数据的过滤以及推荐、分类等多种 AI 应用。\n\n关键证据：\n- 它是 AI 原生的向量搜索和语义搜索引擎，可用于从非结构化数据中提取有意义的信息 [1]\n- 提供生产级服务，通过 API 存储、搜索和管理向量点……[2]\n- 支持稠密向量语义相似性搜索、稀疏向量全文搜索和多向量搜索……[3]",
  "sources": [
    {
      "source": "data/raw/qdrant-docs.md",
      "section_path": "",
      "snippet": "Qdrant Docs\nSource: https://qdrant.tech/documentation/..."
    },
    {
      "source": "data/raw/aiapp-05-qdrant-readme.md",
      "section_path": "",
      "snippet": "Vector Search Engine for the next generation of AI applications..."
    }
  ],
  "trace": [
    { "step": "query_analysis", "status": "completed", "detail": { "question_type": "general" } },
    { "step": "retrieval_planning", "status": "completed", "detail": { "retrieval_strategy": "standard_retrieval", "top_k": 7 } },
    { "step": "retrieval", "status": "completed", "detail": { "top_k": 7, "duration_seconds": 0.2, "document_count": 7 } },
    { "step": "generate_answer", "status": "completed", "detail": { "attempt": 1, "duration_seconds": 42.01 } }
  ]
}
```

要点：检索 7 条上下文（耗时约 0.2s），生成约 42s，答案带 `[n]` 角标与来源引用，全链路 trace 可复盘。

## 4. Agent 多步 loop 问答（重点）

这条请求展示 Agent 的多步 ReAct 循环：模型基于 native function calling 自主多轮决策，
本次先把对比问题拆解，再分别检索两个框架，最后综合作答。

命令：

```bash
curl -sS -X POST http://127.0.0.1:8002/agent/run \
  -H "Content-Type: application/json" \
  -d '{"question":"LangChain 和 LlamaIndex 有什么区别？"}'
```

实测响应（摘录，总耗时约 97.96s）：

```json
{
  "answer": "## LangChain 与 LlamaIndex 的主要区别\n\n根据检索到的信息，这两个都是用于构建 LLM 应用的开源框架，但侧重点和核心能力不同：\n\n- LangChain：构建智能体（Agents）与 LLM 驱动的工作流，核心是“链式”组合，预构建 Agent 架构、丰富第三方集成 [1][2][6]\n- LlamaIndex：面向数据增强（RAG），完整工具链「数据摄取 → 索引 → 查询」，提供两级 API [3][4][5]\n\n简单理解：LangChain 偏「Agent 与工作流编排」，LlamaIndex 偏「私有数据增强」，两者可互补使用。",
  "selected_tool": "retrieval_tool",
  "tool_status": "success",
  "termination_reason": "final_answer",
  "steps": [
    { "round": 1, "status": "tool_executed", "tool_name": "question_decompose_tool", "tool_args": { "question": "LangChain 和 LlamaIndex 有什么区别？" }, "tool_status": "success" },
    { "round": 2, "status": "tool_executed", "tool_name": "retrieval_tool", "tool_args": { "question": "什么是 LangChain" }, "tool_status": "success" },
    { "round": 3, "status": "tool_executed", "tool_name": "retrieval_tool", "tool_args": { "question": "LlamaIndex 框架的主要功能" }, "tool_status": "success" },
    { "round": 4, "status": "final_answer", "tool_name": null, "tool_args": null }
  ],
  "sources": [
    { "source": "data/raw/langchain-docs.md" },
    { "source": "data/raw/03-langchain-README.md" },
    { "source": "data/raw/llamaindex-docs.md" },
    { "source": "data/raw/aiapp-03-llamaindex-readme.md" }
  ],
  "trace": [
    { "step": "analyze_question", "status": "completed", "detail": { "needs_retrieval": true, "question_type": "comparison" } },
    { "step": "agent_loop", "status": "final_answer", "detail": { "termination_reason": "final_answer", "steps": "（见上方 steps）" } }
  ]
}
```

要点（这条最能体现「多步 agent」）：

- **模型自主多轮编排**：第 1 轮自己决定先用 `question_decompose_tool` 拆题，第 2、3 轮分别检索 LangChain 与 LlamaIndex，第 4 轮不再调工具、直接综合作答——循环方向由模型决定，不是 if/else。
- **`termination_reason: final_answer`**：正常收尾（非撞步数上限、非失败）。
- **`steps` 4 轮逐轮可观测**：每轮调了哪个工具、参数、状态都有记录。
- **`sources` 跨轮聚合**：综合了两次检索的 21 条命中（6 个唯一来源），引用合并返回。
- **延迟**：多步每轮一次 LLM 调用，本次 4 轮共约 97.96s，显著高于单步问答——这是多步自主性的成本。

## 当前验证到的链路

```text
上传 → 入库 → 检索 → 生成答案 → 返回来源 → Agent 多步 loop 工具编排
```

本次运行的最强证据：

- 上传与入库接受 Markdown 文件并落库。
- `/ask` 返回有依据的答案、来源引用与 RAG trace。
- `/agent/run` 走多步 loop：模型自主拆题 → 多轮检索 → 综合作答，返回
  `selected_tool`、`tool_status`、`termination_reason`、逐轮 `steps` 与 Agent trace。

## 已知边界

- 本次为单一代表性问题的本地 demo，不是大规模质量或延迟基准。
- 延迟未做优化：RAG 单次生成约 42s，Agent 多步 4 轮约 98s（瓶颈在 LLM 生成）。
- Agent 每轮只执行第一个工具调用；无跨会话长期记忆。
- 工具层无重试；向量检索的有限重试在 RAG 服务层（贴近真实失败点）。
