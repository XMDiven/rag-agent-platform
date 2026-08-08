# request-id 贯穿调用链

## 解决的问题

一次 Agent 请求跨 3 个进程、5 次以上外部调用（BFF → Agent → RAG → Qdrant / Ollama / LLM）。改动前每一段各自打日志，并发时多个请求的日志行交错在一起，**无法判断哪条 `ask.generation` 属于哪次请求**，也就无法回答「刚才那次为什么慢」。

## 实现

核心在 `rag/src/rag_app/infrastructure/request_context.py`。放在 `rag_app` 是因为 `agent_app` 已经依赖它（`retrieval_tool` 直接调用 `ask_question`），两个服务共用同一份实现，不需要新建共享包。

| 组成 | 说明 |
|---|---|
| `RequestIdMiddleware` | 纯 ASGI 中间件。读取 `X-Request-ID`，没有就生成 12 位十六进制；响应头回传。**不用 `BaseHTTPMiddleware`**——它会包一层响应流，对本项目核心的 NDJSON 流式输出有干扰风险 |
| `_request_id` ContextVar | 同进程内自动携带，不需要给任何函数加参数 |
| `RequestIdFilter` | 挂在 root handler 上，自动给每条日志贴 id，**日志语句一行都不用改** |
| `sanitize_request_id` | 只接受 `[A-Za-z0-9._-]{1,64}`。传入的 id 会被写进日志，不过滤的话换行符可以伪造日志行 |
| 前端 `app/request-id.ts` | BFF 生成或透传，同一次用户操作三个服务共用一个 id；400/502 错误响应也带 id |

## 验证

### 真实请求的完整时间线

```bash
curl -X POST http://localhost:8002/agent/run \
  -H 'x-request-id: demo-trace-1' \
  -d '{"question":"对比 Chroma 和 Qdrant，分别适合怎样的 RAG 项目？"}'

grep demo-trace-1 agent.log
```

单次 grep 即可得到跨服务的完整链路（节选）：

```
13:40:03 httpx      [request_id=demo-trace-1] POST api.moonshot.cn/v1/chat/completions 200
13:40:04 httpx      [request_id=demo-trace-1] POST localhost:11434/api/embed 200
13:40:04 ask_service[request_id=demo-trace-1] ask.retrieval completed top_k=7 attempt=1 duration_seconds=1.15
13:40:08 ask_service[request_id=demo-trace-1] ask.generation completed attempt=1 duration_seconds=4.15
13:40:08 ask_service[request_id=demo-trace-1] ask.retrieval completed top_k=7 attempt=1 duration_seconds=0.35
13:40:13 ask_service[request_id=demo-trace-1] ask.generation completed attempt=1 duration_seconds=4.86
13:40:20 ask_service[request_id=demo-trace-1] ask.generation completed attempt=1 duration_seconds=4.93
```

这次请求跑了 3 轮检索 + 生成，每轮耗时、重试次数（`attempt=1` 表示没有重试）一目了然。第三方库（httpx、langchain_openai）的日志同样带上了 id，因为 filter 挂在 root handler。

### 自动化测试

`rag/tests/infrastructure/test_request_context.py`（9 条）与前端 `agent-stream-route.test.ts` 新增 1 条，覆盖：

- **同步路由（跑在 FastAPI 线程池里）能读到 id** —— 这是最关键的一条，contextvar 能否穿透线程池不能靠假设
- **同步流式生成器能读到 id**
- 请求之间不串号
- 恶意 id（含空格/换行）被拒绝并改用自动生成的 id
- BFF 透传合法 id、拒绝非法 id，并在响应头回传

全量：Python 297 passed，前端 25 passed。

## 现在可以回答的问题

- 某次请求为什么慢：哪一轮、检索还是生成、有没有重试
- 用户报障时给出响应头里的 id，可直接定位
- **重试实际发生频率**——这是决定「应用层与 SDK 两层重试该砍哪层」的前提数据（见 `agent/experiments/reports/concurrency/upstream_timeout_2026-08-08.md` 的残留问题）

## 尚未做的

- **token / 成本计量**：仍未统计任何 token 用量。
- **指标端点**：没有 `/metrics`，目前只能靠 grep 日志，无法看聚合趋势。
- 日志是纯文本而非 JSON；接入日志系统时需要改成结构化输出。
- 未做采样或日志量控制；`httpx` 的 INFO 日志在高流量下会很吵。
