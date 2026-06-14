# RAG 学习项目

**语言 / Language：中文 | [English](#rag-learning-project-english)**

这是一个面向学习的 Retrieval-Augmented Generation（RAG）项目，用来探索从文档摄入到最终回答生成的完整后端链路：

```text
原始文档 -> 摄入 -> 切分 -> 向量嵌入 -> Qdrant 检索 -> LLM 生成回答 -> 引用来源
```

项目支持 Markdown 和 PDF 文档，能够通过本地目录或上传接口保存语料，构建 Qdrant 向量索引，提供 FastAPI `/ask` 问答接口和 `/ask/stream` 流式问答接口，并包含轻量级评估脚本和 LLM-as-Judge 结构化评分，用于检查检索质量、最终回答质量和 groundedness。

## 技术栈

- FastAPI：HTTP API
- LangChain：文档处理、检索和生成编排
- Qdrant：向量数据库
- `langchain-ollama`：Ollama embedding 接入
- `langchain-openai`：OpenAI 兼容聊天模型客户端
- Pytest：自动化测试

## 核心功能

- 从 `data/raw` 摄入 Markdown 和 PDF 文件
- 通过 `/documents/upload` 和 `/documents/upload/batch` 上传 Markdown 和 PDF 文件到本地语料目录
- 通过 `/documents/ingest` 将已上传的单个 Markdown 或 PDF 文件解析、切分并写入 Qdrant
- 按稳定的来源元数据切分文档
- 使用确定性的 chunk ID 将切片写入 Qdrant
- 通过 `/ask` 接口提问，或通过 `/ask/stream` 流式接收生成内容
- 返回带结构化来源引用的 grounded answer
- 使用 golden source cases 评估检索结果
- 评估最终回答的基本内容、来源契约和回归风险
- 使用 LLM-as-Judge 对回答的 relevance、completeness、groundedness 和 format 做结构化评分
- 使用 Prompt A/B 报告对比 `qa_prompt_v1` 和 `qa_prompt_v2`
- 通过 Prompt Eval API 查询历史 judge report、最新 Prompt 对比指标，并支持同步小样本评测运行

## 项目结构

```text
data/raw/            原始来源文档
experiments/         检索和回答质量实验记录
src/rag_app/app/                 FastAPI 路由和应用入口
src/rag_app/config/              运行时配置
src/rag_app/ingestion/           加载器、切分器和元数据处理
src/rag_app/infrastructure/      Embedding、LLM 和向量库客户端
src/rag_app/retrieval/           Retriever 构建
src/rag_app/generation/          Prompt、上下文格式化和回答生成
src/rag_app/services/            应用服务层
src/rag_app/scripts/             索引构建和评估脚本
tests/               单元测试和脚本测试
```

## 环境

使用 `AI_DEV` conda 环境：

```bash
conda activate AI_DEV
```

应用需要以下环境变量：

```text
QDRANT_URL
QDRANT_COLLECTION
EMBEDDING_BASE_URL
EMBEDDING_MODEL
LLM_BASE_URL
LLM_MODEL_ID
MOONSHOT_API_KEY or OPENAI_API_KEY
```

可以通过 Docker Compose 启动 Qdrant：

```bash
docker compose up -d qdrant
```

也可以构建 RAG 应用镜像：

```bash
docker build -t rag-app .
```

从容器启动 RAG API：

```bash
docker run --rm --env-file .env \
  -e QDRANT_URL=http://host.docker.internal:6333 \
  -e EMBEDDING_BASE_URL=http://host.docker.internal:11434 \
  -p 8001:8001 \
  rag-app
```

说明：容器里的 `localhost` 指向容器自身，不是宿主机。如果 Qdrant 或 Ollama embedding 服务运行在宿主机上，macOS/OrbStack/Docker Desktop 通常使用 `host.docker.internal` 访问。若这些服务运行在其他网络或 compose service 中，请把 `QDRANT_URL`、`EMBEDDING_BASE_URL` 改成容器可访问的地址。

## 快速开始

1. 创建并激活 Python 环境：

```bash
conda create -n AI_DEV python=3.11 -y
conda activate AI_DEV
pip install -r requirements-dev.txt
pip install -e . --no-deps
```

2. 配置环境变量：

```bash
cp .env.example .env
```

根据你的 Qdrant、embedding 模型和 LLM 服务配置编辑 `.env`。不要提交真实 API key。

3. 准备语料：

把需要入库的 Markdown 或 PDF 文件放到 `data/raw/`：

```text
data/raw/
  example.md
  example.pdf
```

也可以在 API 启动后通过上传接口保存文件：

```bash
curl -X POST http://127.0.0.1:8001/documents/upload \
  -F "file=@data/raw/example.md;type=text/markdown"
```

批量上传多个文件：

```bash
curl -X POST http://127.0.0.1:8001/documents/upload/batch \
  -F "files=@data/raw/example.md;type=text/markdown" \
  -F "files=@data/raw/example.pdf;type=application/pdf"
```

4. 启动 Qdrant 并构建索引：

```bash
docker compose up -d qdrant
python -m rag_app.scripts.reset_index
python -m rag_app.scripts.build_index
```

如果你已经通过 API 上传了单个文件，也可以只入库这个文件：

```bash
curl -X POST http://127.0.0.1:8001/documents/ingest \
  -H "Content-Type: application/json" \
  -d '{"filename":"example.md"}'
```

5. 启动 API 并提问：

```bash
uvicorn rag_app.app.main:app --host 127.0.0.1 --port 8001 --reload
```

```bash
curl -X POST http://127.0.0.1:8001/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"What does retrieval augmented generation combine?"}'
```

6. 运行验证：

```bash
pytest tests/ -q
python -m rag_app.scripts.run_eval
```

说明：`run_eval` 依赖已经构建好的 Qdrant 索引和本地实验语料。若你使用自己的文档，需要同步调整 `experiments/retrieval_eval_cases.json`。

## 构建索引

重置当前 Qdrant collection：

```bash
conda run -n AI_DEV python -m rag_app.scripts.reset_index
```

从支持的原始文档构建向量索引：

```bash
conda run -n AI_DEV python -m rag_app.scripts.build_index
```

## 运行 API

启动 FastAPI：

```bash
conda run -n AI_DEV uvicorn rag_app.app.main:app --host 127.0.0.1 --port 8001 --reload
```

上传 Markdown 或 PDF 文件：

```bash
curl -X POST http://127.0.0.1:8001/documents/upload \
  -F "file=@data/raw/example.pdf;type=application/pdf"
```

上传响应示例：

```json
{
  "filename": "example.pdf",
  "saved_path": "example.pdf",
  "content_type": "application/pdf"
}
```

批量上传 Markdown 或 PDF 文件：

```bash
curl -X POST http://127.0.0.1:8001/documents/upload/batch \
  -F "files=@data/raw/example.md;type=text/markdown" \
  -F "files=@data/raw/example.pdf;type=application/pdf"
```

批量上传响应示例：

```json
{
  "files": [
    {
      "filename": "example.md",
      "saved_path": "example.md",
      "content_type": "text/markdown"
    },
    {
      "filename": "example.pdf",
      "saved_path": "example.pdf",
      "content_type": "application/pdf"
    }
  ]
}
```

上传接口只负责把文件保存到 `data/raw/`。上传新文件后，可以调用 `/documents/ingest` 入库单个文件，或重新运行 `build_index` 批量重建索引。

入库已上传的单个文件：

```bash
curl -X POST http://127.0.0.1:8001/documents/ingest \
  -H "Content-Type: application/json" \
  -d '{"filename":"example.pdf"}'
```

入库响应示例：

```json
{
  "path": "/Users/mdiven/Code/Projects/rag-agent-platform/rag/data/raw/example.pdf",
  "document_count": 100,
  "chunk_count": 452,
  "stored_count": 452
}
```

发送问题：

```bash
curl -X POST http://127.0.0.1:8001/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"What does retrieval augmented generation combine?"}'
```

流式提问：

```bash
curl -N -X POST http://127.0.0.1:8001/ask/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"What does retrieval augmented generation combine?"}'
```

响应包含：

- `answer`：带编号引用的生成回答，例如 `[1]`
- `sources`：用于支撑回答的结构化来源元数据和片段
- `trace`：问题分析、检索规划、检索和生成步骤的轻量执行轨迹

示例响应：

```json
{
  "answer": "RAG combines retrieval with generation so the model can answer using external context. [1]",
  "sources": [
    {
      "source": "data/raw/rag-paper.pdf",
      "section_path": "unknown",
      "snippet": "Retrieval-Augmented Generation combines a retriever with a generator..."
    }
  ],
  "trace": [
    {
      "step": "query_analysis",
      "status": "completed",
      "detail": {
        "normalized_question": "What does retrieval augmented generation combine?",
        "needs_retrieval": true,
        "reason": "normal knowledge question, use retrieval",
        "question_type": "general"
      }
    },
    {
      "step": "retrieval_planning",
      "status": "completed",
      "detail": {
        "question_type": "general",
        "retrieval_strategy": "standard_retrieval",
        "retrieval_query": "What does retrieval augmented generation combine?",
        "top_k": 7,
        "reason": "general knowledge questions use standard retrieval"
      }
    },
    {
      "step": "retrieval",
      "status": "completed",
      "detail": {
        "retrieval_strategy": "standard_retrieval",
        "retrieval_query": "What does retrieval augmented generation combine?",
        "top_k": 7,
        "document_count": 5,
        "retrieved_sources": [
          "data/raw/rag-paper.pdf"
        ]
      }
    },
    {
      "step": "generate_answer",
      "status": "completed",
      "detail": {}
    }
  ]
}
```

流式响应使用 NDJSON，每行是一个事件：

```jsonl
{"type":"answer_delta","content":"RAG combines retrieval with generation"}
{"type":"sources","sources":[{"source":"data/raw/rag-paper.pdf","section_path":"unknown","snippet":"..."}]}
{"type":"trace","trace":[{"step":"generate_answer","status":"completed","detail":{"streaming":true}}]}
{"type":"done"}
```

## 验证

运行测试套件：

```bash
conda run -n AI_DEV pytest tests/ -q
```

运行统一 RAG 评估：

```bash
conda run -n AI_DEV python -m rag_app.scripts.run_eval
```

按 Prompt 版本运行评估：

```bash
conda run -n AI_DEV env QA_PROMPT_VERSION=qa_prompt_v1 python -m rag_app.scripts.run_eval
conda run -n AI_DEV env QA_PROMPT_VERSION=qa_prompt_v2 python -m rag_app.scripts.run_eval
```

`run_eval` 会执行两类检查：

- 检索评估：验证 golden questions 是否检索到预期来源文档。
- 回答评估：验证 `/ask` 返回非空回答、有效来源、预期来源命中，并且不会把来源元数据泄漏到回答文本中。

当 `QA_PROMPT_VERSION=qa_prompt_v2` 时，回答评估还会检查结构化输出契约：答案需要包含 `Direct answer:`、`Key evidence:` 和 `Limitations:` 三个部分。

每次运行还会生成一份 JSON 评估报告：

```text
experiments/evaluation_runs/
```

报告包含当前 `RETRIEVAL_TOP_K`、`prompt_version`、检索评估汇总、回答评估汇总、每个 case 的来源路径，以及失败 case 列表。`prompt_version` 用来标识当前回答生成 prompt 的版本；只有当 prompt 策略发生变化时，才需要升级版本号，例如 `qa_prompt_v1` 到 `qa_prompt_v2`。该文件用于保留可复现的评估证据，方便后续比较不同检索配置或 prompt 版本的效果。

运行 LLM-as-Judge 回答质量评估：

```bash
conda run --no-capture-output -n AI_DEV python -m rag_app.scripts.evaluate_answers_with_judge
```

该脚本会复用 golden questions 的在线问答流程，再把问题、回答和本次检索返回的 sources 交给 judge prompt，输出 relevance、completeness、groundedness 和 format 四个维度的 1-5 分结构化结果。

LLM-as-Judge 报告默认写入：

```text
experiments/judge_runs/
```

当前代表性报告说明见 `experiments/llm_judge_report.md`。
Prompt A/B 对比结论见 `experiments/prompt_comparison_report.md`。

API 启动后，也可以通过 Prompt Eval API 查询历史评测结果：

```bash
curl http://127.0.0.1:8001/prompt-evals/reports
```

查询指定 judge report：

```bash
curl http://127.0.0.1:8001/prompt-evals/reports/<run_id>
```

查询最新 `qa_prompt_v1` 和 `qa_prompt_v2` 对比指标：

```bash
curl http://127.0.0.1:8001/prompt-evals/comparison/latest
```

同步触发一次小样本 LLM-as-Judge 评测，并将报告写入 `experiments/judge_runs/`：

```bash
curl -X POST http://127.0.0.1:8001/prompt-evals/run \
  -H "Content-Type: application/json" \
  -d '{"prompt_version":"qa_prompt_v2","case_limit":1}'
```

`GET /prompt-evals/reports`、`GET /prompt-evals/reports/{run_id}` 和 `GET /prompt-evals/comparison/latest` 只读取已有历史报告；`POST /prompt-evals/run` 会在 HTTP 请求内同步触发评测，适合本地 demo 和小样本验证，不适合长耗时生产任务。生产环境应改成后台任务或任务队列。

仓库只提交有代表性的 baseline 报告。新的本地评估报告默认会被忽略；如果某次运行需要作为新基线保存，可以使用 `git add -f` 手动加入。

## 当前评估基线

当前 golden set 包含 11 个覆盖 Markdown、PDF、中文转述和多来源对比的代表性问题。
当前已验证检索配置为 `RETRIEVAL_TOP_K = 7`。最近一次已验证基线为：

```text
pytest: 85 passed
retrieval eval: 11/11 passed
answer eval: 11/11 passed
judge eval: 11/11 passed
```

## 说明

- `run_eval` 假设 Qdrant 索引已经构建完成。
- 修改 ingestion、chunking、embedding 或 metadata 行为后，需要重新构建索引。
- 只修改 prompt 时，不需要重新构建索引。

---

# RAG Learning Project (English)

**Language / 语言：[中文](#rag-学习项目) | English**

This repository is a learning-focused Retrieval-Augmented Generation (RAG) project built around a full backend pipeline:

```text
raw documents -> ingestion -> chunking -> embeddings -> Qdrant retrieval -> LLM answer -> cited sources
```

The project supports Markdown and PDF documents, saves source files either from the local corpus directory or through an upload endpoint, builds a Qdrant vector index, exposes FastAPI `/ask` and `/ask/stream` endpoints, and includes lightweight evaluation scripts plus LLM-as-Judge structured scoring for retrieval quality, final answer quality, and groundedness.

## Tech Stack

- FastAPI for the HTTP API
- LangChain for document processing, retrieval, and generation orchestration
- Qdrant as the vector database
- Ollama embeddings via `langchain-ollama`
- OpenAI-compatible chat model client via `langchain-openai`
- Pytest for automated tests

## Core Features

- Ingest Markdown and PDF files from `data/raw`
- Upload Markdown and PDF files through `/documents/upload` and `/documents/upload/batch`
- Ingest a single uploaded Markdown or PDF file into Qdrant through `/documents/ingest`
- Chunk documents with stable source metadata
- Store chunks in Qdrant with deterministic chunk IDs
- Ask questions through `/ask`, or stream generated content through `/ask/stream`
- Return grounded answers with structured source citations
- Evaluate retrieval with golden source cases
- Evaluate final answer output for basic answer and source contract regressions
- Use LLM-as-Judge to score relevance, completeness, groundedness, and format with a structured schema
- Compare `qa_prompt_v1` and `qa_prompt_v2` with a Prompt A/B report
- Query historical judge reports and latest Prompt comparison metrics through the Prompt Eval API, and trigger synchronous small-sample eval runs

## Project Structure

```text
data/raw/            Source documents
experiments/         Retrieval and answer-quality experiment records
src/rag_app/app/                 FastAPI routers and app entrypoint
src/rag_app/config/              Runtime configuration
src/rag_app/ingestion/           Loaders, chunkers, and metadata handling
src/rag_app/infrastructure/      Embedding, LLM, and vector store clients
src/rag_app/retrieval/           Retriever construction
src/rag_app/generation/          Prompt, context formatting, and answer generation
src/rag_app/services/            Application service layer
src/rag_app/scripts/             Indexing and evaluation scripts
tests/               Unit and script tests
```

## Environment

Use the `AI_DEV` conda environment:

```bash
conda activate AI_DEV
```

The application expects these environment variables:

```text
QDRANT_URL
QDRANT_COLLECTION
EMBEDDING_BASE_URL
EMBEDDING_MODEL
LLM_BASE_URL
LLM_MODEL_ID
MOONSHOT_API_KEY or OPENAI_API_KEY
```

Qdrant can be started with Docker Compose:

```bash
docker compose up -d qdrant
```

You can also build the RAG application image:

```bash
docker build -t rag-app .
```

Start the RAG API from the image:

```bash
docker run --rm --env-file .env \
  -e QDRANT_URL=http://host.docker.internal:6333 \
  -e EMBEDDING_BASE_URL=http://host.docker.internal:11434 \
  -p 8001:8001 \
  rag-app
```

Note: `localhost` inside a container points to the container itself, not the host machine. If Qdrant or the Ollama embedding service runs on the host, macOS/OrbStack/Docker Desktop usually reaches it through `host.docker.internal`. If those services run on another network or as compose services, set `QDRANT_URL` and `EMBEDDING_BASE_URL` to addresses reachable from the container.

## Quickstart

1. Create and activate the Python environment:

```bash
conda create -n AI_DEV python=3.11 -y
conda activate AI_DEV
pip install -r requirements-dev.txt
pip install -e . --no-deps
```

2. Configure environment variables:

```bash
cp .env.example .env
```

Edit `.env` for your Qdrant, embedding model, and LLM service settings. Do not commit real API keys.

3. Prepare source documents:

Put Markdown or PDF files into `data/raw/`:

```text
data/raw/
  example.md
  example.pdf
```

You can also upload files after the API starts:

```bash
curl -X POST http://127.0.0.1:8001/documents/upload \
  -F "file=@data/raw/example.md;type=text/markdown"
```

Batch upload multiple files:

```bash
curl -X POST http://127.0.0.1:8001/documents/upload/batch \
  -F "files=@data/raw/example.md;type=text/markdown" \
  -F "files=@data/raw/example.pdf;type=application/pdf"
```

4. Start Qdrant and build the index:

```bash
docker compose up -d qdrant
python -m rag_app.scripts.reset_index
python -m rag_app.scripts.build_index
```

If you uploaded a single file through the API, you can ingest only that file:

```bash
curl -X POST http://127.0.0.1:8001/documents/ingest \
  -H "Content-Type: application/json" \
  -d '{"filename":"example.md"}'
```

5. Start the API and ask a question:

```bash
uvicorn rag_app.app.main:app --host 127.0.0.1 --port 8001 --reload
```

```bash
curl -X POST http://127.0.0.1:8001/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"What does retrieval augmented generation combine?"}'
```

6. Run verification:

```bash
pytest tests/ -q
python -m rag_app.scripts.run_eval
```

Note: `run_eval` depends on a built Qdrant index and the local experiment corpus. If you use your own documents, update `experiments/retrieval_eval_cases.json` accordingly.

## Build The Index

Reset the current Qdrant collection:

```bash
conda run -n AI_DEV python -m rag_app.scripts.reset_index
```

Build the vector index from supported raw documents:

```bash
conda run -n AI_DEV python -m rag_app.scripts.build_index
```

## Run The API

Start FastAPI:

```bash
conda run -n AI_DEV uvicorn rag_app.app.main:app --host 127.0.0.1 --port 8001 --reload
```

Upload a Markdown or PDF file:

```bash
curl -X POST http://127.0.0.1:8001/documents/upload \
  -F "file=@data/raw/example.pdf;type=application/pdf"
```

Example upload response:

```json
{
  "filename": "example.pdf",
  "saved_path": "example.pdf",
  "content_type": "application/pdf"
}
```

Batch upload Markdown or PDF files:

```bash
curl -X POST http://127.0.0.1:8001/documents/upload/batch \
  -F "files=@data/raw/example.md;type=text/markdown" \
  -F "files=@data/raw/example.pdf;type=application/pdf"
```

Example batch upload response:

```json
{
  "files": [
    {
      "filename": "example.md",
      "saved_path": "example.md",
      "content_type": "text/markdown"
    },
    {
      "filename": "example.pdf",
      "saved_path": "example.pdf",
      "content_type": "application/pdf"
    }
  ]
}
```

The upload endpoints only save files into `data/raw/`. After uploading a new file, call `/documents/ingest` to ingest one file, or run `build_index` again to rebuild the index in batch.

Ingest a single uploaded file:

```bash
curl -X POST http://127.0.0.1:8001/documents/ingest \
  -H "Content-Type: application/json" \
  -d '{"filename":"example.pdf"}'
```

Example ingest response:

```json
{
  "path": "/Users/mdiven/Code/Projects/rag-agent-platform/rag/data/raw/example.pdf",
  "document_count": 100,
  "chunk_count": 452,
  "stored_count": 452
}
```

Ask a question:

```bash
curl -X POST http://127.0.0.1:8001/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"What does retrieval augmented generation combine?"}'
```

Ask a streaming question:

```bash
curl -N -X POST http://127.0.0.1:8001/ask/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"What does retrieval augmented generation combine?"}'
```

The response contains:

- `answer`: the generated answer with numbered citations such as `[1]`
- `sources`: structured source metadata and snippets used to support the answer
- `trace`: lightweight execution trace for query analysis, retrieval planning, retrieval, and generation

Example response:

```json
{
  "answer": "RAG combines retrieval with generation so the model can answer using external context. [1]",
  "sources": [
    {
      "source": "data/raw/rag-paper.pdf",
      "section_path": "unknown",
      "snippet": "Retrieval-Augmented Generation combines a retriever with a generator..."
    }
  ],
  "trace": [
    {
      "step": "query_analysis",
      "status": "completed",
      "detail": {
        "normalized_question": "What does retrieval augmented generation combine?",
        "needs_retrieval": true,
        "reason": "normal knowledge question, use retrieval",
        "question_type": "general"
      }
    },
    {
      "step": "retrieval_planning",
      "status": "completed",
      "detail": {
        "question_type": "general",
        "retrieval_strategy": "standard_retrieval",
        "retrieval_query": "What does retrieval augmented generation combine?",
        "top_k": 7,
        "reason": "general knowledge questions use standard retrieval"
      }
    },
    {
      "step": "retrieval",
      "status": "completed",
      "detail": {
        "retrieval_strategy": "standard_retrieval",
        "retrieval_query": "What does retrieval augmented generation combine?",
        "top_k": 7,
        "document_count": 5,
        "retrieved_sources": [
          "data/raw/rag-paper.pdf"
        ]
      }
    },
    {
      "step": "generate_answer",
      "status": "completed",
      "detail": {}
    }
  ]
}
```

The streaming response uses NDJSON, with one event per line:

```jsonl
{"type":"answer_delta","content":"RAG combines retrieval with generation"}
{"type":"sources","sources":[{"source":"data/raw/rag-paper.pdf","section_path":"unknown","snippet":"..."}]}
{"type":"trace","trace":[{"step":"generate_answer","status":"completed","detail":{"streaming":true}}]}
{"type":"done"}
```

## Verification

Run the test suite:

```bash
conda run -n AI_DEV pytest tests/ -q
```

Run the unified RAG evaluation:

```bash
conda run -n AI_DEV python -m rag_app.scripts.run_eval
```

Run evaluation by Prompt version:

```bash
conda run -n AI_DEV env QA_PROMPT_VERSION=qa_prompt_v1 python -m rag_app.scripts.run_eval
conda run -n AI_DEV env QA_PROMPT_VERSION=qa_prompt_v2 python -m rag_app.scripts.run_eval
```

`run_eval` performs two checks:

- Retrieval evaluation: verifies expected source documents are retrieved for golden questions.
- Answer evaluation: verifies `/ask` returns non-empty answers, valid sources, expected source hits, and does not leak source metadata into the answer text.

When `QA_PROMPT_VERSION=qa_prompt_v2`, answer evaluation also checks the structured-output contract: the answer must include `Direct answer:`, `Key evidence:`, and `Limitations:` sections.

Each run also writes a JSON evaluation report:

```text
experiments/evaluation_runs/
```

The report includes the current `RETRIEVAL_TOP_K`, `prompt_version`, retrieval and answer evaluation summaries, per-case source paths, and failed cases. `prompt_version` identifies the answer-generation prompt version; update it only when the prompt strategy changes, for example from `qa_prompt_v1` to `qa_prompt_v2`. These reports preserve reproducible evaluation evidence for comparing retrieval settings or prompt versions over time.

Run the LLM-as-Judge answer-quality evaluation:

```bash
conda run --no-capture-output -n AI_DEV python -m rag_app.scripts.evaluate_answers_with_judge
```

This script reuses the online question-answering flow for the golden questions, then sends the question, generated answer, and retrieved sources to the judge prompt. The judge returns structured 1-5 scores for relevance, completeness, groundedness, and format.

LLM-as-Judge reports are written to:

```text
experiments/judge_runs/
```

The current representative report is summarized in `experiments/llm_judge_report.md`.
The Prompt A/B comparison is summarized in `experiments/prompt_comparison_report.md`.

After the API starts, you can also query historical evaluation results through the Prompt Eval API:

```bash
curl http://127.0.0.1:8001/prompt-evals/reports
```

Query a specific judge report:

```bash
curl http://127.0.0.1:8001/prompt-evals/reports/<run_id>
```

Query the latest `qa_prompt_v1` and `qa_prompt_v2` comparison metrics:

```bash
curl http://127.0.0.1:8001/prompt-evals/comparison/latest
```

Trigger a synchronous small-sample LLM-as-Judge run and write the report to `experiments/judge_runs/`:

```bash
curl -X POST http://127.0.0.1:8001/prompt-evals/run \
  -H "Content-Type: application/json" \
  -d '{"prompt_version":"qa_prompt_v2","case_limit":1}'
```

`GET /prompt-evals/reports`, `GET /prompt-evals/reports/{run_id}`, and `GET /prompt-evals/comparison/latest` only read existing historical reports; `POST /prompt-evals/run` triggers evaluation synchronously inside the HTTP request. It is intended for local demos and small-sample validation, not long-running production jobs. Production deployments should use a background task or queue.

Only representative baseline reports are committed. New local evaluation reports are ignored by default; use `git add -f` if a run should become a new baseline.

## Current Evaluation Baseline

The current golden set contains 11 representative questions across Markdown, PDF, Chinese paraphrase, and multi-source comparison cases.
The current verified retrieval setting is `RETRIEVAL_TOP_K = 7`. The latest verified baseline is:

```text
pytest: 85 passed
retrieval eval: 11/11 passed
answer eval: 11/11 passed
judge eval: 11/11 passed
```

## Notes

- `run_eval` assumes the Qdrant index has already been built.
- Rebuild the index after changing ingestion, chunking, embedding, or metadata behavior.
- Prompt-only changes do not require rebuilding the index.
