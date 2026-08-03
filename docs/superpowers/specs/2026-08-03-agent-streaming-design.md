# Agent 真流式协议与流式 Chat 设计

## 目标

为现有 Agent 增加独立的流式执行路径，使浏览器能在同一次请求中逐轮看到工具步骤，并在最终回答生成时接收真实模型 chunk。保留现有 `POST /agent/run` 和 `POST /api/agent` 的一次性 JSON 行为，不破坏已有调用方。

本任务对应 `resume_alignment.md` 的 B-2b。完成标准是流式事件顺序、错误语义和断流行为均有自动化测试，并在真实多工具问题下看到步骤和答案逐步出现。

## 不在本次范围

- 不引入 Vercel AI SDK、WebSocket 或新的第三方依赖。
- 不实现会话历史、用户系统、限流、缓存或 token 成本统计。
- 不用把完整答案切片后延迟发送的“假流式”冒充模型真流式。
- 不删除或改变现有非流式接口。
- 不为本任务重构无关的 RAG、Agent 工具或页面视觉样式。

## 方案比较

### 方案 A：原生 `fetch + ReadableStream + NDJSON`（采用）

FastAPI 使用 `StreamingResponse` 输出每行一个 JSON 事件；Next.js Route Handler 原样转发 `ReadableStream`；浏览器用 `TextDecoder` 和行缓冲解析事件。

优点是适合当前 POST 请求和自定义事件类型，没有新依赖，协议、错误和断流行为完全可测试。代价是需要自己实现小型 NDJSON parser 和 React 状态归并。

### 方案 B：SSE

SSE 有成熟的事件语义，但浏览器原生 `EventSource` 只支持 GET。继续使用 POST 时仍需用 `fetch` 手动解析 SSE，复杂度并不低于 NDJSON，因此不采用。

### 方案 C：Vercel AI SDK

它适合标准文本生成流，但当前流中还包含 Agent 工具步骤、来源和终止原因。为适配 SDK 改造协议会扩大范围并隐藏底层学习价值，留到协议稳定后再评估。

## 架构与兼容性

新增两条接口：

- FastAPI：`POST /agent/run/stream`
- Next.js BFF：`POST /api/agent/stream`

现有接口保持不变：

- FastAPI：`POST /agent/run` 继续返回 `AgentRunResponse` JSON。
- Next.js BFF：`POST /api/agent` 继续转发一次性 JSON。

流式调用链：

```text
React page
→ POST /api/agent/stream
→ Next.js Route Handler
→ POST /agent/run/stream
→ FastAPI StreamingResponse
→ streaming Agent loop
→ NDJSON events
→ BFF byte-for-byte re-stream
→ browser NDJSON parser
→ React state updates
```

## 事件协议

媒体类型固定为：

```text
application/x-ndjson
```

每个事件占一行，以 `\n` 结尾。统一外层字段：

```json
{"version":1,"type":"step","data":{}}
```

第一版定义五种事件。

### `step`

在一次工具执行结束后发送，沿用现有 Agent step 字段：

```json
{
  "version": 1,
  "type": "step",
  "data": {
    "round": 1,
    "status": "tool_executed",
    "tool_name": "retrieval_tool",
    "tool_args": {"question": "什么是 RAG？"},
    "tool_status": "success"
  }
}
```

工具失败仍属于 `step`，使用 `status="tool_failed"` 和 `tool_status="failed"`。它不会自动终止整个 Agent；失败结果仍按现有逻辑回灌模型，让模型决定恢复或收尾。事件不包含工具异常原文。

### `answer_delta`

仅承载模型本次真实流式调用返回的非空文本 chunk：

```json
{"version":1,"type":"answer_delta","data":{"text":"RAG 是"}}
```

前端按到达顺序拼接 `text`。不得先调用 `invoke()` 得到完整答案再人工切片。

### `sources`

最终回答完成后发送一次，内容与现有 `/agent/run` 的 `sources` 一致：

```json
{"version":1,"type":"sources","data":{"sources":[{"source":"rag.md"}]}}
```

### `error`

流开始后的终止性错误通过事件表达：

```json
{
  "version": 1,
  "type": "error",
  "data": {
    "code": "agent_stream_failed",
    "message": "Agent 流式执行失败，请重试"
  }
}
```

只允许稳定错误码和安全文案，不发送异常消息、API key、认证 URL、Prompt 或请求调试信息。

### `done`

每条正常或失败的已建立流都必须以一个 `done` 结束：

```json
{
  "version": 1,
  "type": "done",
  "data": {
    "termination_reason": "final_answer",
    "selected_tool": "retrieval_tool",
    "tool_status": "success"
  }
}
```

事件顺序约束：

```text
step* → answer_delta+ → sources → done
step* → error → done
```

非检索或降级路径可以没有 `step` 和 `sources`，但仍必须产生答案或错误，并以 `done` 结束。`done` 只能出现一次，出现后不得再发送事件。

## Agent 真流式实现

### 工具调用与文本模式

现有 loop 每轮调用 `tool_calling_llm.invoke(messages)`。流式版本改用 `tool_calling_llm.stream(messages)` 并累加 `AIMessageChunk`。LangChain 的 chunk 相加会组装分段的 `tool_call_chunks`，最终得到结构化 `tool_calls`。

每轮维护输出模式：

- 首个有效 `tool_call_chunk` 将本轮设为 `tool` 模式。后续聚合完整工具名和参数，流结束后执行第一个工具，发送 `step`，并回灌 `ToolMessage`。
- 首个非空文本 chunk 将本轮设为 `answer` 模式。每个文本 chunk 立即发送 `answer_delta`，流结束后发送 `sources` 和 `done`。
- 同一轮同时出现答案文本与工具调用视为 `mixed_model_output`，发送安全 `error` 和失败 `done`。第一版不尝试撤回已发送文本。

当前配置关闭模型 thinking，减少 reasoning content 与 tool-call 混合。事件提取只处理正常文本 content，不把 reasoning block 发给浏览器。

### 多工具调用

保持现有行为：每轮只执行模型返回的第一个工具调用，其余调用写入 `skipped` ToolMessage，避免在本任务中改变 Agent 编排语义。

### 步数耗尽

达到 `AGENT_MAX_STEPS` 后，复用现有“禁用工具、强制收尾”策略，但使用 `llm.bind_tools(..., tool_choice="none").stream(messages)` 获取真实答案 chunk。成功时 `done.termination_reason="max_steps"`；空输出时发送安全 fallback `answer_delta`，并以 `termination_reason="failed"` 结束。

### 降级路径

- 空问题或 `analyze_query()` 判断应跳过 loop：复用 `run_agent_once()`，把其已有结果编码成 `answer_delta`、可选 `sources` 和 `done`。这条路径没有模型 token 流，不伪装为流式生成。
- loop 在发送任何事件前失败：允许复用 `run_agent_once()` 降级。
- loop 在发送至少一个事件后失败：不能重置客户端状态，发送安全 `error` 和失败 `done`，不再启动单步降级。

流式服务需要显式记录是否已经发送事件，保证上述边界可测试。

## FastAPI 层

新增流式 schema/编码模块、流式 service 和路由。路由返回同步事件生成器包装的 `StreamingResponse`。Starlette 会在线程池中迭代同步生成器，因此现有同步 LLM/Qdrant 客户端不会直接阻塞事件循环。

响应头至少包括：

```text
Content-Type: application/x-ndjson
Cache-Control: no-cache
X-Content-Type-Options: nosniff
```

请求体仍复用 `AgentRunRequest`。请求 JSON/Pydantic 校验在响应开始前失败时，继续使用正常 HTTP 4xx；响应开始后的错误只能使用 `error` 事件。

## Next.js BFF 层

新增 `frontend/app/api/agent/stream/route.ts`，服务端读取：

```text
AGENT_STREAM_API_URL
```

本地默认值为 `http://localhost:8002/agent/run/stream`；Compose 设置为 `http://agent-api:8002/agent/run/stream`。浏览器不能提交或覆盖上游 URL，变量不使用 `NEXT_PUBLIC_`。

BFF 只负责：

- 校验 `{question: string}`；
- POST 到上游；
- 保留上游 HTTP 状态；
- 将 `upstreamResponse.body` 直接放入新的 `Response`；
- 设置 NDJSON、no-cache 和 nosniff 响应头；
- 在上游尚未建立响应时连接失败，返回稳定的 502 JSON。

BFF 不调用 `response.json()` 或 `response.text()`，也不解析、重组或缓冲业务事件。

## 浏览器解析与 React 状态

新增无 React 依赖的 `agent-stream.ts`：

- `AgentStreamEvent` TypeScript 判别联合；
- `parseAgentEvent(line)`，拒绝未知版本、未知类型和无效 data；
- `readAgentStream(stream, onEvent)`，使用 `ReadableStreamDefaultReader`；
- `TextDecoder.decode(value, {stream: true})`，正确处理被网络 chunk 拆开的中文 UTF-8 字节；
- 保留未形成完整行的尾部 buffer；
- 流结束时若仍有非空完整 JSON 尾行则解析；
- 未收到 `done` 就 EOF，抛出稳定的 `stream_ended_early` 错误。

页面改为维护增量状态：

- `answer`：追加 `answer_delta.text`；
- `steps`：追加 `step.data`；
- `sources`：由 `sources` 事件一次设置；
- `terminationReason`、`selectedTool`、`toolStatus`：由 `done` 设置；
- `loading`：提交时为真，收到 `done` 或异常时结束；
- `error`：收到 `error` 事件或解析/网络异常时设置。

页面在流进行中就渲染 answer 和 steps，不再等待完整 `AgentRunResponse`。使用 `AbortController` 让组件卸载时停止读取；用户主动停止按钮不在第一版范围。

## 断流、取消与背压

- 浏览器 EOF 前未收到 `done`：显示“连接意外中断，请重试”，保留已经收到的部分答案和步骤。
- 浏览器取消或组件卸载：调用 `AbortController.abort()`，不把主动取消显示成服务错误。
- BFF 直接转发 Web Stream，让运行时背压沿 reader/writer 传播，不做内存累积。
- 同步 SDK 已开始的单次阻塞调用不保证能被浏览器断开立即取消；第一版保证不再读取或启动下一轮，真正取消上游请求留给后续 async 客户端改造。

## 测试策略

严格按 TDD 实现，不用真实 LLM/Qdrant 完成单元测试。

### Python

- 事件模型和 NDJSON 编码每行可独立反序列化。
- 模型文本 chunk 产生真实 `answer_delta`，拼接结果等于完整答案。
- 分段 `tool_call_chunks` 被组装后只执行一次工具，并发送一个 `step`。
- 工具失败发送 `tool_failed` step，错误原文不进入事件，模型仍可继续。
- 多轮顺序为 `step* → answer_delta+ → sources → done`。
- 混合文本/工具输出、流中异常、空最终答案产生稳定安全事件。
- max-steps 使用无工具 stream 收尾。
- FastAPI 流式接口媒体类型、事件顺序和空问题路径正确。
- 现有 `/agent/run` 测试保持不变并继续通过。

### TypeScript

- BFF 校验、上游地址、状态码和响应 body 透传。
- BFF 不读取完整上游 body；测试 reader 能先取得第一块，再让上游发送第二块。
- parser 处理一块多行、一行多块和中文 UTF-8 字节拆分。
- parser 拒绝未知版本/类型和无效 data。
- 正常流必须收到一个 `done`；提前 EOF 报稳定错误。
- React 状态归并按事件更新 answer、steps、sources 和 done metadata。
- 页面 fake-stream 联调证明第一条 step 在流结束前可见，答案按 chunk 增长。

## 验收

自动化命令：

```bash
uv run pytest -q
cd frontend
npm test
npm run lint
npx tsc --noEmit
npm run build
cd ..
docker compose --env-file .env.example config --quiet
git diff --check
```

真实环境手工验收：

1. 启动 Qdrant、RAG、Agent 和 frontend。
2. 提问需要多工具的 `LangChain 和 LlamaIndex 有什么区别？`。
3. 确认第一个工具步骤在最终回答完成前出现在页面。
4. 确认答案内容随模型 chunk 增长，不是结束后一次出现。
5. 确认最终来源、终止原因和工具状态与非流式接口语义一致。
6. 人为中断连接，确认页面保留部分结果并显示明确断流状态。

## 工程权衡

- **正确性优先于视觉效果**：只有直接消费模型 `stream()` 才称为真流式。
- **兼容性优先于复用接口名**：新增流式端点，避免破坏已有 JSON 客户端。
- **协议清晰优先于框架便利**：第一版手写少量 NDJSON 解析，换取 Agent 事件完全可控。
- **范围控制优先于彻底异步化**：同步生成器在线程池执行，取消上游同步调用和 async 客户端迁移留到企业级并发任务。
- **可维护性**：事件 schema、编码器、parser 和状态归并分别放在独立模块，避免把协议逻辑塞进路由或 React 页面。
