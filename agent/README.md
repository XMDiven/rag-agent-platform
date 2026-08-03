# Agent

`agent` 是建立在 `rag` 子项目之上的轻量 Agent 编排层。它当前不实现完整 ReAct、多 Agent 协作或通用工具平台，而是先把最小可验证链路做清楚：

```text
analyze_question -> plan_tool -> execute_tool -> build AgentState -> return result with trace
```

## 当前能力

- 调用 RAG 的 `analyze_query()` 判断问题是否需要检索
- 根据 `question_type` 和问题文本规则选择 `retrieval_tool`、`summary_tool`、`question_decompose_tool` 或 `fallback_tool`
- 通过 `executor` 执行工具
- `retrieval_tool` 会调用 `rag_app.services.ask_service.ask_question`
- `summary_tool` 会对用户输入文本执行本地确定性摘要
- `question_decompose_tool` 会对比较类和多子问题输入做规则式结构化拆解，并对子问题逐个调用 RAG 检索后聚合结果
- 工具注册表为每个工具定义 `input_schema` 和 `output_schema`，明确工具调用契约
- Service 层返回结构化 `AgentRunResult`
- API 层返回适合前端和用户消费的 `answer`、`sources`、`selected_tool`、`tool_status`、`tool_output` 和 `trace`
- 通过 `GET /agent/tools` 暴露工具能力列表和输入输出契约，便于调试、前端能力发现和后续受控 tool calling
- 返回 Agent 层 trace，记录分析、规划和执行步骤
- 提供 12-case 离线评测集和评测 runner，检查工具轨迹、结束原因、来源约束与延迟
- 使用 `AgentState` 保存 question、analysis、plan、tool_result 和 trace，明确 Agent 内部状态流转
- Agent 层记录工具执行成功或失败；RAG 检索与生成重试由 RAG 服务内部处理
- 通过 FastAPI 暴露 `POST /agent/run` 接口
- 提供 `GET /health` 健康检查接口

## 模块结构

```text
src/agent_app/
  app/
    main.py            # FastAPI 应用入口
    routers/health.py  # GET /health 健康检查接口
    routers/run.py     # POST /agent/run 接口
    routers/tools.py   # GET /agent/tools 工具能力发现接口
  schemas/run.py    # Agent API 请求和响应结构
  orchestration/
    planner.py       # 根据问题分析结果选择工具
    executor.py      # 执行工具并包装 ToolResult
    state.py         # Agent 内部状态对象
  tools/
    registry.py      # 工具注册表和输入输出契约
    question_decompose.py  # 规则式问题拆解工具
    retrieval.py     # 调用 RAG 问答服务的工具适配层
    summary.py       # 本地摘要工具
  scripts/
    run_agent.py       # Agent CLI 演示入口
    evaluate_agent.py  # Agent 离线评测 runner
  service.py         # Agent 对外统一入口 run_agent(question)
```

目录边界：

- `app/` 只处理 HTTP 入口。
- `schemas/` 只放 API 请求和响应结构。
- `service.py` 负责串起一次 Agent run。
- `orchestration/` 放规划、执行和状态流转。
- `tools/` 放工具注册表、工具输入输出契约和具体工具适配层。

## 执行流程

`run_agent(question)` 是当前 Agent 的统一入口：

```python
from agent_app.service import run_agent

result = run_agent("What is RAG?")
```

返回对象包含：

- `plan`: 工具选择结果
- `tool_result`: 工具执行结果
- `trace`: Agent 层执行轨迹

注意：这是 service 层的内部对象，适合测试和调试 Agent 状态流转。FastAPI 的 `/agent/run` 不直接暴露完整内部对象，而是返回更稳定的对外响应结构。

## 工具规划策略

当前 planner 是规则式规划，不调用 LLM。它根据 RAG `query_analyzer` 返回的 `question_type` 选择工具：

| question_type | tool | 说明 |
| --- | --- | --- |
| `empty` | `fallback_tool` | 空问题不执行检索 |
| `summary` | `summary_tool` | 对用户输入文本做本地摘要 |
| `general` | `retrieval_tool` | 使用 RAG 知识库检索回答 |

此外，planner 会优先识别包含 `分别`、`对比`、`比较`、`compare`、`difference`、`vs` 等明显拆解信号的问题。如果命中规则，会选择 `question_decompose_tool`。这是本地规则式拆解，不调用 LLM；executor 会对子问题逐个调用 RAG 检索工具，并聚合 `answer`、`sources` 和 `sub_results`。

普通知识问题成功路径示例：

```json
[
  {
    "step": "analyze_question",
    "status": "completed",
    "detail": {
      "needs_retrieval": true,
      "question_type": "general",
      "reason": "normal knowledge question, use retrieval"
    }
  },
  {
    "step": "plan_tool",
    "status": "completed",
    "detail": {
      "tool_name": "retrieval_tool",
      "reason": "question requires knowledge retrieval"
    }
  },
  {
    "step": "execute_tool",
    "status": "success",
    "detail": {
      "tool_name": "retrieval_tool",
      "attempts": [
        {
          "attempt": 1,
          "status": "success"
        }
      ]
    }
  }
]
```

`retrieval_tool` 调用 RAG，可能遇到向量库、LLM 或网络类临时失败。Agent executor 不做粗粒度整体重试；向量检索和回答生成的有限重试由 RAG 服务层处理。工具失败时，`tool_result.status` 会变为 `failed`，并返回错误类型、错误信息和 attempts 记录：

```json
{
  "tool_name": "retrieval_tool",
  "status": "failed",
  "output": {
    "error_type": "RuntimeError",
    "error": "rag unavailable"
  },
  "attempts": [
    {
      "attempt": 1,
      "status": "failed",
      "error_type": "RuntimeError",
      "error": "rag unavailable"
    }
  ]
}
```

总结类问题会走 `summary_tool`：

```json
[
  {
    "step": "analyze_question",
    "status": "completed",
    "detail": {
      "needs_retrieval": true,
      "question_type": "summary",
      "reason": "summary question, use retrieval"
    }
  },
  {
    "step": "plan_tool",
    "status": "completed",
    "detail": {
      "tool_name": "summary_tool",
      "reason": "question asks for summarization"
    }
  },
  {
    "step": "execute_tool",
    "status": "success",
    "detail": {
      "tool_name": "summary_tool",
      "attempts": [
        {
          "attempt": 1,
          "status": "success"
        }
      ]
    }
  }
]
```

## 和 RAG 的关系

Agent trace 记录上层编排过程：

```text
Agent 为什么选择工具、执行了哪个工具、执行状态是什么
```

RAG trace 记录工具内部过程：

```text
query analysis、retrieval planning、retrieve、generate answer
```

因此，RAG 是 Agent 当前调用的第一个真实工具，Agent 负责上层决策和执行状态追踪。

## 运行测试

```bash
cd agent
uv run pytest tests/ -q
```

## CLI 演示

可以通过最小 CLI 入口运行 Agent 编排流程：

```bash
cd agent
uv run python -m agent_app.scripts.run_agent ""
```

空字符串会走 `fallback_tool`，适合验证 CLI 和 Agent trace 输出。普通知识问题会走 `retrieval_tool`，可能触发 RAG 检索和 LLM 调用。

总结类问题会走 `summary_tool`：

```bash
uv run python -m agent_app.scripts.run_agent "请总结 LangChain 的用途"
```

响应中的 `tool_result` 会包含：

```json
{
  "tool_name": "summary_tool",
  "status": "success",
  "output": {
    "summary": "请总结 LangChain 的用途"
  }
}
```

## FastAPI 接口

启动 Agent API：

```bash
cd agent
uv run uvicorn agent_app.app.main:app --host 127.0.0.1 --port 8002 --reload
```

调用 `POST /agent/run`：

```bash
curl -X POST http://127.0.0.1:8002/agent/run \
  -H "Content-Type: application/json" \
  -d '{"question": ""}'
```

空字符串会走 `fallback_tool`，适合验证 API、Agent trace 和响应结构。普通知识问题会走 `retrieval_tool`，可能触发 RAG 检索和 LLM 调用。

调用总结工具：

```bash
curl -X POST http://127.0.0.1:8002/agent/run \
  -H "Content-Type: application/json" \
  -d '{"question": "请总结 LangChain 的用途"}'
```

调用问题拆解工具：

```bash
curl -X POST http://127.0.0.1:8002/agent/run \
  -H "Content-Type: application/json" \
  -d '{"question": "LangChain 和 LlamaIndex 分别适合做什么？"}'
```

查询 Agent 工具能力：

```bash
curl http://127.0.0.1:8002/agent/tools
```

`GET /agent/tools` 返回当前已注册工具的 `name`、`description`、`input_schema` 和 `output_schema`。该接口只做能力发现，不执行工具、不调用 RAG，也不代表已经接入 LLM Function Calling。

### `/agent/run` 响应结构

API 层返回的是给前端或调用方使用的结构，不直接暴露 service 层内部的 `plan` 和 `tool_result`：

| 字段 | 说明 |
| --- | --- |
| `answer` | 最终给用户展示的回答；从工具输出中的 `answer`、`summary` 或 `error` 提取 |
| `sources` | RAG 工具返回的来源列表；非检索工具默认返回空列表 |
| `selected_tool` | planner 选中的工具名 |
| `tool_status` | 工具执行状态，通常是 `success` 或 `failed` |
| `tool_output` | 原始工具输出，保留给调试和前端扩展使用 |
| `trace` | Agent 层执行轨迹，记录分析、规划和执行步骤 |

空问题会走 `fallback_tool`：

```json
{
  "answer": "No retrieval is needed for this question.",
  "sources": [],
  "selected_tool": "fallback_tool",
  "tool_status": "success",
  "tool_output": {
    "answer": "No retrieval is needed for this question.",
    "sources": []
  },
  "trace": [
    {
      "step": "analyze_question",
      "status": "completed",
      "detail": {
        "needs_retrieval": false,
        "question_type": "empty",
        "reason": "empty question"
      }
    },
    {
      "step": "plan_tool",
      "status": "completed",
      "detail": {
        "tool_name": "fallback_tool",
        "reason": "question does not require retrieval"
      }
    },
    {
      "step": "execute_tool",
      "status": "success",
      "detail": {
        "tool_name": "fallback_tool",
        "attempts": [
          {
            "attempt": 1,
            "status": "success"
          }
        ]
      }
    }
  ]
}
```

总结问题会走 `summary_tool`：

```json
{
  "answer": "请总结 LangChain 的用途",
  "sources": [],
  "selected_tool": "summary_tool",
  "tool_status": "success",
  "tool_output": {
    "summary": "请总结 LangChain 的用途"
  },
  "trace": [
    {
      "step": "analyze_question",
      "status": "completed",
      "detail": {
        "needs_retrieval": true,
        "question_type": "summary",
        "reason": "summary question, use retrieval"
      }
    },
    {
      "step": "plan_tool",
      "status": "completed",
      "detail": {
        "tool_name": "summary_tool",
        "reason": "question asks for summarization"
      }
    },
    {
      "step": "execute_tool",
      "status": "success",
      "detail": {
        "tool_name": "summary_tool",
        "attempts": [
          {
            "attempt": 1,
            "status": "success"
          }
        ]
      }
    }
  ]
}
```

比较类或多子问题会走 `question_decompose_tool`。该工具会先规则式拆解问题，再对子问题逐个调用 RAG 检索工具，并聚合最终回答、来源和子问题结果：

```json
{
  "answer": "1. LangChain 适合做什么？\nLangChain answer\n\n2. LlamaIndex 适合做什么？\nLlamaIndex answer",
  "sources": [],
  "selected_tool": "question_decompose_tool",
  "tool_status": "success",
  "tool_output": {
    "answer": "1. LangChain 适合做什么？\nLangChain answer\n\n2. LlamaIndex 适合做什么？\nLlamaIndex answer",
    "sources": [],
    "sub_questions": [
      "LangChain 适合做什么？",
      "LlamaIndex 适合做什么？"
    ],
    "sub_results": [
      {
        "question": "LangChain 适合做什么？",
        "status": "success",
        "answer": "LangChain answer",
        "sources": [],
        "attempts": [
          {
            "attempt": 1,
            "status": "success"
          }
        ]
      },
      {
        "question": "LlamaIndex 适合做什么？",
        "status": "success",
        "answer": "LlamaIndex answer",
        "sources": [],
        "attempts": [
          {
            "attempt": 1,
            "status": "success"
          }
        ]
      }
    ],
    "reason": "question contains explicit multi-part intent",
    "decomposition_strategy": "comparison"
  },
  "trace": [
    {
      "step": "analyze_question",
      "status": "completed",
      "detail": {
        "needs_retrieval": true,
        "question_type": "general",
        "reason": "normal knowledge question, use retrieval"
      }
    },
    {
      "step": "plan_tool",
      "status": "completed",
      "detail": {
        "tool_name": "question_decompose_tool",
        "reason": "question contains comparison or multi-part intent"
      }
    },
    {
      "step": "execute_tool",
      "status": "success",
      "detail": {
        "tool_name": "question_decompose_tool",
        "attempts": [
          {
            "attempt": 1,
            "status": "success"
          }
        ],
        "decomposition_strategy": "comparison",
        "sub_question_count": 2
      }
    }
  ]
}
```

普通知识问题会走 `retrieval_tool`。此时 `tool_output` 会保留 RAG 的原始返回，通常包含 `answer`、`sources` 和 RAG 内部 trace；API 顶层会额外提取 `answer` 和 `sources` 方便调用方直接使用：

```json
{
  "answer": "RAG answer",
  "sources": [],
  "selected_tool": "retrieval_tool",
  "tool_status": "success",
  "tool_output": {
    "answer": "RAG answer",
    "sources": [],
    "trace": []
  },
  "trace": [
    {
      "step": "analyze_question",
      "status": "completed",
      "detail": {
        "needs_retrieval": true,
        "question_type": "general",
        "reason": "normal knowledge question, use retrieval"
      }
    },
    {
      "step": "plan_tool",
      "status": "completed",
      "detail": {
        "tool_name": "retrieval_tool",
        "reason": "question requires knowledge retrieval"
      }
    },
    {
      "step": "execute_tool",
      "status": "success",
      "detail": {
        "tool_name": "retrieval_tool",
        "attempts": [
          {
            "attempt": 1,
            "status": "success"
          }
        ]
      }
    }
  ]
}
```

健康检查接口：

```bash
curl http://127.0.0.1:8002/health
```

返回：

```json
{
  "status": "ok",
  "service": "agent"
}
```

## 当前边界

当前 `agent` 是轻量编排层，不是完整 Agent 平台。它还没有实现：

- LLM 摘要工具
- 网络搜索工具
- 并行执行同一轮的多个工具调用
- 跨会话长期记忆
- 多 Agent 协作

现阶段应先用 [`experiments/README.md`](experiments/README.md) 中的离线评测基线验证真实模型的工具选择、正常结束率和延迟，再根据失败 case 决定是否扩展这些能力。
