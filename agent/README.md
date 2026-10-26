# Agent

`agent` 是建立在 `rag` 子项目之上的轻量 Agent 编排层。非空问题默认使用 native function calling，由模型自主选择工具并决定是否继续执行：

```text
analyze_question -> 多步 loop（模型决策 -> 工具执行 -> 结果回灌）-> 最终回答
```

## 当前能力

- 调用 RAG 的 `analyze_query()` 判断问题是否需要检索
- 多步 loop 每轮调用绑定工具的模型；单步路径通过可配置 planner 选择工具，并保留规则兜底
- 通过 `executor` 执行工具
- `retrieval_tool` 会调用 `rag_app.services.ask_service.ask_question`
- `summary_tool` 调用配置的 LLM 生成摘要，并将 `summary` 正文传给下一轮模型
- `question_decompose_tool` 会对比较类和多子问题输入做规则式结构化拆解，并对子问题逐个调用 RAG 检索后聚合结果
- 工具注册表为每个工具定义 `input_schema` 和 `output_schema`，明确工具调用契约
- Service 层返回结构化 `AgentRunResult`
- API 层返回适合前端和用户消费的 `answer`、`sources`、`selected_tool`、`tool_status`、`tool_output` 和 `trace`
- 通过 `GET /agent/tools` 暴露工具能力列表和输入输出契约，便于调试、前端能力发现和后续受控 tool calling
- 返回 Agent 层 trace 和逐轮 `steps`；单步路径记录分析、规划和执行步骤
- 提供 12-case 离线评测集和评测 runner，检查工具轨迹、结束原因、来源约束与延迟
- 提供独立 Agent LLM-as-Judge runner，评价最终答案的相关性、完整性、证据支撑与格式
- 单步路径使用 `AgentState` 保存 question、analysis、plan、tool_result 和 trace；多步路径记录逐轮 `steps` 和聚合来源
- Agent 层记录工具执行成功或失败；问答 HTTP 请求中的 RAG 检索、生成与 SDK 自动重试关闭，内部工具和最终回答共用请求预算，CLI 保留原有行为。配置与边界见 [问答请求预算](../docs/request-budgets.md)
- 通过 FastAPI 暴露兼容的一次性 JSON 接口 `POST /agent/run`
- 通过 `POST /agent/run/stream` 逐行输出版本化 NDJSON 事件；模型最终回答使用真实 token 流
- 提供 `GET /health` 健康检查接口

## 模块结构

```text
src/agent_app/
  app/
    main.py            # FastAPI 应用入口
    routers/health.py  # GET /health 健康检查接口
    routers/run.py     # JSON 与 NDJSON Agent 接口
    routers/tools.py   # GET /agent/tools 工具能力发现接口
  schemas/run.py    # Agent API 请求和响应结构
  orchestration/
    planner.py       # 根据问题分析结果选择工具
    executor.py      # 执行工具并包装 ToolResult
    state.py         # Agent 内部状态对象
    loop.py          # 同步多步 native function calling loop
    streaming.py     # 保留的同步流式 loop
    async_streaming.py # 当前 HTTP 异步流式 loop 与断连取消
    finetuned_router.py # 单步 planner 的可选微调路由适配
  tools/
    registry.py      # 工具注册表和输入输出契约
    question_decompose.py  # 规则式问题拆解工具
    retrieval.py     # 调用 RAG 问答服务的工具适配层
    summary.py       # LLM 摘要工具
  scripts/
    run_agent.py       # Agent CLI 演示入口
    evaluate_agent.py  # Agent 离线评测 runner
    evaluate_agent_answers.py # 最终答案 Judge
    evaluate_agent_stream.py # HTTP 流式协议与延迟评测
    compare_strategies.py # 直接 RAG、固定流程与 Agent 的同题对照入口
  service.py         # Agent 对外统一入口 run_agent(question)
  streaming_service.py # 同步/异步流入口与安全错误映射
```

目录边界：

- `app/` 只处理 HTTP 入口。
- `schemas/` 只放 API 请求和响应结构。
- `service.py` 负责串起一次 Agent run。
- `orchestration/` 放规划、执行和状态流转。
- `tools/` 放工具注册表、工具输入输出契约和具体工具适配层。

## 执行流程

`run_agent(question)` 是 JSON 接口、CLI、编排评测和答案 Judge 使用的同步入口：

```python
from agent_app.service import run_agent

result = run_agent("What is RAG?")
```

返回对象包含：

- `plan`: 工具选择结果
- `tool_result`: 工具执行结果
- `trace`: Agent 层执行轨迹
- `termination_reason`: 最终回答、步数上限、失败或单步执行的结束原因
- `steps`: 多步 loop 的逐轮记录；单步路径为空列表

注意：这是 service 层的内部对象，适合测试和调试 Agent 状态流转。FastAPI 的 `/agent/run` 不直接暴露完整内部对象，而是返回更稳定的对外响应结构。

| 入口 | 非空问题 | 未处理的 loop 异常 | 空问题 |
| --- | --- | --- | --- |
| `/agent/run`、`run_agent()`、CLI、编排评测、答案 Judge | `run_agent_loop()` 同步执行 | 转入 `run_agent_once()` 单步规划 | 直接走 `fallback_tool` |
| `/agent/run/stream`、页面、HTTP 流式评测 | `stream_agent_ndjson_async()` → `stream_agent_loop_async()` | 输出安全 `error`，再输出 `termination_reason=failed` 的 `done` | 返回单步结果事件 |

流式接口在首事件前出错和输出部分答案后出错时，都不会另起同步降级请求。客户端断开时取消等待、关闭模型迭代器并停止后续轮次；已开始的同步工具只能在协作取消检查点或上游超时后停止，详见 [`docs/agent-stream-cancellation.md`](../docs/agent-stream-cancellation.md)。

`stream_agent_events()` 和 `stream_agent_loop()` 是保留的同步流式实现，部分单元测试仍使用它们；它们不再是当前 HTTP 路由和流式评测的执行入口。

同步和流式 loop 共用 `compact_tool_payload()` 回灌工具结果。检索结果除生成摘要和全局编号的 `sources` 文件名外，还传递 `evidence`：包含相同引用编号、来源、章节和原始 snippet。缺少文件名的来源仍占据 `unknown` 编号，避免与最终聚合来源错位；缺少原文的来源不生成证据条目。最终回答应以对应 snippet 为事实依据，不能仅凭生成摘要或文件名引用，也不能扩写出证据未说明的协作分工。工具正文视为数据，不作为指令执行。新增上下文只使用本次工具返回的片段，不另行读取整篇文档；这会增加模型输入，实际质量、费用和延迟仍需真实运行验证。

## 工具规划策略

多步 loop 使用 `bind_tools(..., tool_choice="auto")`，由当前配置的聊天模型逐轮决策，不经过单步 planner，也不调用微调 router。

`plan_tool()` 用于 `run_agent_once()` 单步路径，选择顺序如下：

1. 空问题直接选择 `fallback_tool`，不调用任何选工具模型。
2. 非空问题默认 `ROUTER_BACKEND=llm`，由 LLM native tool calling 选择工具。
3. 设置 `ROUTER_BACKEND=finetuned` 后，先调用 `FINETUNED_ROUTER_URL` 的 `/v1/route`；默认服务地址是 `http://127.0.0.1:8000`。微调服务不可用或返回无效选择时退回 LLM。
4. LLM 选择也失败时，才按问题类型和文本规则兜底。

因此，设置 `ROUTER_BACKEND=finetuned` 不会替换正常多步 loop 的模型。通过现有 HTTP 接口使用时，它只会在非空的同步 loop 抛出异常、转入单步规划后参与选择；当前异步流的正常和异常路径都不调用它。

规则兜底的选择如下：

| question_type | tool | 说明 |
| --- | --- | --- |
| `empty` | `fallback_tool` | 空问题不执行检索 |
| `summary` | `summary_tool` | 对用户输入文本做 LLM 摘要 |
| `general` | `retrieval_tool` | 使用 RAG 知识库检索回答 |

规则兜底会优先识别包含 `分别`、`对比`、`比较`、`compare`、`difference`、`vs` 等拆解信号的问题，选择 `question_decompose_tool`。工具内部的拆解使用本地规则；executor 会对子问题逐个调用 RAG 检索与生成，并聚合 `answer`、`sources` 和 `sub_results`，因此整个工具仍可能产生模型调用。

以下 trace 是单步规则兜底选择 `retrieval_tool` 后的示意；正常多步执行的 trace 使用 `analyze_question` 和 `agent_loop`，逐轮决策在 `steps` 中：

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

单步规则兜底对总结类问题选择 `summary_tool` 时，trace 示意如下：

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

空字符串会走 `fallback_tool`，适合验证 CLI 和 Agent trace 输出。非空问题进入多步 loop，由模型选择是否调用工具，可能触发 RAG 检索和多次 LLM 调用。

以下输入可用于观察模型是否选择摘要工具，具体轨迹由模型决定：

```bash
uv run python -m agent_app.scripts.run_agent "请总结以下文本：LangChain 帮助开发者组合模型、提示词和检索组件。"
```

正常多步执行的 `tool_result` 包装最终回答，结构示意如下；工具调用过程在 `steps` 中：

```json
{
  "tool_name": "agent_loop",
  "status": "success",
  "output": {
    "answer": "模型根据工具结果生成的最终摘要",
    "sources": []
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

调用真正的流式接口时使用 `curl -N` 禁用客户端输出缓冲：

```bash
curl -N -X POST http://127.0.0.1:8002/agent/run/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"LangChain 和 LlamaIndex 有什么区别？"}'
```

响应媒体类型是 `application/x-ndjson`，每行是一个完整 JSON 事件：

| 事件 | 作用 |
| --- | --- |
| `step` | 一轮工具执行结束后立即报告工具名、参数和状态 |
| `answer_delta` | 模型生成的答案增量，可直接追加到界面 |
| `sources` | 回答结束后返回跨轮聚合来源 |
| `error` | 返回稳定错误码和安全消息，不暴露内部异常文本 |
| `done` | 流的终止哨兵，包含结束原因和最终工具状态 |

所有事件都带 `version: 1`。调用方应一直读取到 `done`；若连接提前结束，应保留已收到的内容，同时将本次结果标为不完整。模型使用异步流，工具执行在线程中运行，因此 `step` 会在该轮工具完成后出现；答案增量在模型生成回答时输出。`error → done` 表示失败结束，即使 HTTP 状态码为 200，也不能当作成功回答。

空字符串会走 `fallback_tool`，适合验证 API、Agent trace 和响应结构。非空问题进入多步 loop，具体工具轨迹由模型决定。

提交总结任务，观察模型选择的工具：

```bash
curl -X POST http://127.0.0.1:8002/agent/run \
  -H "Content-Type: application/json" \
  -d '{"question": "请总结以下文本：LangChain 帮助开发者组合模型、提示词和检索组件。"}'
```

提交比较任务，观察模型是否拆解问题：

```bash
curl -X POST http://127.0.0.1:8002/agent/run \
  -H "Content-Type: application/json" \
  -d '{"question": "LangChain 和 LlamaIndex 分别适合做什么？"}'
```

查询 Agent 工具能力：

```bash
curl http://127.0.0.1:8002/agent/tools
```

`GET /agent/tools` 返回当前已注册工具的 `name`、`description`、`input_schema` 和 `output_schema`。该接口只做能力发现，不执行工具、不调用 RAG；多步 loop 会使用注册信息构造 native function calling 的工具定义。

### `/agent/run` 响应结构

API 层返回的是给前端或调用方使用的结构，不直接暴露 service 层内部的 `plan` 和 `tool_result`：

| 字段 | 说明 |
| --- | --- |
| `answer` | 最终给用户展示的回答；从工具输出中的 `answer`、`summary` 或 `error` 提取 |
| `sources` | 多步执行中跨轮聚合的来源；单步执行取工具返回的来源 |
| `selected_tool` | 多步路径为最后执行的工具；未调用工具时为 `fallback_tool` 标签；单步路径为 planner 选中的工具 |
| `tool_status` | 多步路径的汇总状态，或单步工具状态；单次工具失败后仍可能由模型恢复 |
| `tool_output` | 多步路径包装最终 `answer`、`sources`；单步路径保留原始工具输出 |
| `trace` | 多步路径记录问题分析和 loop 摘要；单步路径记录分析、规划和执行 |
| `termination_reason` | `final_answer`、`max_steps`、`failed` 或 `single_step` |
| `steps` | 多步 loop 的逐轮记录；单步路径为空列表 |

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

以下总结和拆解 JSON 是单步规则兜底响应的结构示意，省略默认的 `termination_reason: single_step` 和 `steps: []`；文本为占位内容，不是当前模型实跑结果。正常多步请求不会固定返回这些工具或 trace。单步选择 `summary_tool` 时：

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

单步规则兜底识别出比较或多子问题信号时选择 `question_decompose_tool`。该工具先按规则拆解，再对子问题逐个调用 RAG，并聚合回答、来源和子问题结果，结构示意如下：

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

单步选择 `retrieval_tool` 时，`tool_output` 保留 RAG 的原始返回，通常包含 `answer`、`sources` 和 RAG 内部 trace；API 顶层提取 `answer` 和 `sources`。下面也是省略默认结束字段的结构示意：

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

- 网络搜索工具
- 并行执行同一轮的多个工具调用
- 跨会话长期记忆
- 多 Agent 协作

现阶段应先用 [`experiments/README.md`](experiments/README.md) 中的离线评测基线验证真实模型的工具选择、正常结束率和延迟，再根据失败 case 决定是否扩展这些能力。
