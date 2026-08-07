# rag-agent-platform

这是一个从 RAG 知识库问答逐步演进到轻量 Agent 编排的 AI 应用工程项目。

## 一次多步自主编排长什么样

问 `LangChain 和 LlamaIndex 有什么区别？`，模型在**一次** `/agent/run` 请求里**自主跑了 4 轮**（基于 native function calling，循环方向由模型决定，不是写死的 if/else）：

| 轮次 | 模型自主决策 | 调用的工具 |
| --- | --- | --- |
| 1 | 先把对比问题拆解 | `question_decompose_tool` |
| 2 | 检索框架 A | `retrieval_tool`（"什么是 LangChain"） |
| 3 | 检索框架 B | `retrieval_tool`（"LlamaIndex 框架的主要功能"） |
| 4 | 信息够了，综合作答 | —（`final_answer`） |

结果 `termination_reason = final_answer`，跨轮聚合 6 个来源，全程 `steps` 逐轮可观测。
真实命令与完整响应见 [`docs/demo/end_to_end.md`](docs/demo/end_to_end.md)。

## 架构

```mermaid
flowchart TD
    U[用户提问] --> BFF[Next.js BFF<br/>POST /api/agent/stream]
    BFF --> EP[FastAPI<br/>POST /agent/run/stream]
    EP --> AQ[analyze_query 问题分析]
    AQ -->|空问题 / loop 异常 → 降级| ONCE[单步编排 run_agent_once]
    AQ --> LOOP[多步 Agent Loop<br/>run_agent_loop]

    LOOP -->|bind_tools, tool_choice=auto| DEC{模型每轮决策}
    DEC -->|选择调工具| DISP[run_tool 工具派发]
    DISP --> RTOOL[retrieval_tool]
    DISP --> STOOL[summary_tool]
    DISP --> QTOOL[question_decompose_tool]
    DISP -->|结果/失败 结构化回灌 ToolMessage| DEC
    DEC -->|不再调工具 / 收尾| OUT[NDJSON 事件流<br/>step + answer_delta + sources + done]

    RTOOL --> RAG

    subgraph RAG [RAG 检索生成链路 /ask]
        direction LR
        ING[摄入 MD/PDF] --> CHK[切分] --> EMB[向量化] --> VDB[(Qdrant)]
        VDB --> RET[相似度检索 top-k] --> GEN[grounded 生成] --> CITE[来源引用]
    end
```

当前仓库结构把 RAG 作为 Agent 系统的第一个可运行子项目。`rag` 子项目已经支持文档摄入、批量上传、向量检索、问答生成、流式问答、来源返回、轻量问题分析、检索规划、执行 trace、Prompt 版本管理、Redis Cache-Aside 精确答案缓存、离线评测、LLM-as-Judge 结构化评分和 Prompt A/B 对比报告。`agent` 子项目在 RAG 之上提供编排层：默认走基于 native function calling 的多步 agent loop（模型自主多轮选择并调用工具、工具结果与失败都回灌模型，由模型决定继续调工具还是收尾），并保留规则式单步编排作为降级路径；同时提供兼容的一次性 JSON 接口 `/agent/run` 和真正逐块输出的 NDJSON 接口 `/agent/run/stream`。

当前扩展重点是补齐 Agent 工程证据：用离线 golden set 持续评估工具轨迹、正常结束率、来源约束和延迟，再根据失败 case 优化 Prompt、步数预算或工具设计。

## 子项目

| 子项目 | 状态 | 说明 |
| --- | --- | --- |
| `rag` | RAG MVP + 轻量编排基线 | 支持 Markdown/PDF 入库、单文件/批量上传、Qdrant 检索、FastAPI 问答、流式返回、来源引用、执行 trace、Prompt 版本、离线评测、LLM-as-Judge 结构化评分和 Prompt A/B 对比报告 |
| `agent` | 多步 Agent loop 编排 | 支持工具注册、问题分析、工具执行、结构化 trace、工具失败处理、native function calling 多步 loop、NDJSON 流式事件、12-case 编排评测和独立答案质量 Judge |
| `frontend` | Agent 演示界面 | 使用 Next.js App Router + TypeScript；浏览器通过同源 `/api/agent/stream` BFF 消费流，在请求完成前逐步展示工具步骤与答案 |

## 当前能力边界

已经完成并有代码或测试支撑：

- RAG：Markdown/PDF 摄入、单文件上传、批量上传、Qdrant 索引、`/ask`、`/ask/stream`、来源引用、RAG trace、Prompt 版本、Redis 共享精确答案缓存（TTL、索引版本失效、fail-open）、离线评估、LLM-as-Judge 评分报告和 Prompt A/B 对比报告。
- Agent：`retrieval_tool`、`summary_tool`、`question_decompose_tool`、`fallback_tool`、基于 native function calling 的多步 agent loop（`run_agent_loop`，工具失败回灌模型由其自主恢复）+ 规则式单步降级（`run_agent_once`）、`run_tool` 工具派发、executor、AgentState、Agent trace、工具失败结构化返回，以及工具轨迹/结束原因/来源/延迟的离线评测 runner。
- Agent 与 Frontend：`/agent/run/stream` 输出版本化 NDJSON 事件，BFF 不缓冲地转发响应体；页面增量渲染 `step` 和 `answer_delta`，最终接收 `sources` 与 `done`。原 `/agent/run` JSON 接口保持兼容。

仍需要补证据后再写进简历的能力：

- 100+ 页 PDF 稳定处理。
- 平均响应时延 2s 内。
- 上下文命中率提升约 20%。
- AutoGen、网络搜索工具。
- 完整 A/B Prompt 对比平台、300+ 样本或 80+ 组实验。

## Docker Compose 一键启动

根目录的 `compose.yaml` 会统一启动以下服务，并通过一次性初始化任务确保 Qdrant collection 存在：

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| Frontend | <http://localhost:3000> | Next.js Agent 问答页面；通过服务端 BFF 访问 Agent API |
| RAG API | <http://localhost:8001> | `/ask`、文档上传和摄入接口 |
| Agent API | <http://localhost:8002> | `/agent/run` JSON 接口、`/agent/run/stream` NDJSON 流接口和工具编排 |
| Qdrant | <http://localhost:6333> | 本地向量数据库 |
| Redis | <http://localhost:6379> | RAG API、Agent API 和索引脚本共享的精确答案缓存与索引版本 |

### 前置条件

- 已安装并启动 Docker。
- 宿主机已安装并启动 Ollama。
- 已下载项目使用的 embedding 模型：

```bash
ollama pull nomic-embed-text
```

Compose 中的后端容器通过 `host.docker.internal:11434` 访问宿主机 Ollama。Linux 如果无法连接，需要让 Ollama 监听容器可访问的地址；只应在可信的本地开发环境中这样配置：

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

### 启动项目

1. 创建本地环境变量文件：

```bash
cp .env.example .env
```

2. 编辑 `.env`，至少填写一个可用的 `MOONSHOT_API_KEY` 或 `OPENAI_API_KEY`。真实 `.env` 已被 Git 忽略，不要把密钥提交到仓库。

3. 构建并启动全部服务：

```bash
docker compose up --build -d
```

如果之前通过 `rag/compose.yaml` 启动过独立 Qdrant，需要先执行 `docker compose -f rag/compose.yaml stop qdrant`，避免宿主机 `6333`、`6334` 端口冲突。停止容器不会删除原有 Qdrant 数据。

4. 检查服务状态：

```bash
docker compose ps
curl http://localhost:8001/health
curl http://localhost:8002/health
```

首次启动会创建空的 Qdrant collection，但不会自动包含知识库文档。服务启动后，需要按照 [`rag/README.md`](rag/README.md) 上传并摄入文档，然后才能进行有知识库上下文的问答。后续启动会保留已有 collection 和索引数据。

停止服务但保留 Qdrant 和上传数据：

```bash
docker compose down
```

如需同时删除本地 Qdrant 索引和上传数据，可以执行 `docker compose down -v`。这是破坏性操作，已有数据会被清除。

## 当前运行方式

端到端演示记录见 [`docs/demo/end_to_end.md`](docs/demo/end_to_end.md)，包含
upload、ingest、ask、stream ask 和 agent run 的真实 `curl` 命令与响应摘录。

本地开发的 Python 依赖由仓库根目录的 uv workspace 统一管理，先同步环境：

```bash
uv sync
```

之后所有 Python 命令都用 `uv run` 前缀执行，不需要手动激活虚拟环境。

进入 RAG 子项目：

```bash
cd rag
```

然后按照 [`rag/README.md`](rag/README.md) 运行。

进入 Agent 子项目：

```bash
cd agent
uv run pytest tests/ -q
```

Agent API 入口：

```bash
cd agent
uv run uvicorn agent_app.app.main:app --reload
```

更多说明见 [`agent/README.md`](agent/README.md)。

前端本地开发默认由服务端 Route Handler 访问
`http://localhost:8002/agent/run/stream`。如需覆盖地址，只把它设置为服务端环境变量，
不要使用 `NEXT_PUBLIC_` 前缀：

```bash
cd frontend
AGENT_STREAM_API_URL=http://localhost:8002/agent/run/stream npm run dev
```

## English

This repository is evolving from a RAG knowledge-base application into a lightweight Agent project. The [`rag`](rag/) module provides document ingestion, single and batch upload, Qdrant retrieval, FastAPI question answering, streaming responses, cited sources, lightweight query analysis, retrieval planning, execution traces, prompt versioning, and evaluation scripts. The [`agent`](agent/) module provides an orchestration layer with question analysis, tool planning, tool execution, a deterministic summary tool, Agent traces, structured tool failure handling, a multi-step agent loop built on native function calling (the model autonomously selects and calls tools across rounds, with tool results and failures fed back to it so it decides whether to keep calling tools or finish) plus a rule-based single-step fallback, a FastAPI `/agent/run` endpoint, and a `/health` endpoint.
