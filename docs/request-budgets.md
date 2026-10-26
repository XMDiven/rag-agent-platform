# 问答请求预算

`POST /ask`、`/ask/stream`、`/agent/run`、`/agent/run/stream` 各自建立一份独立预算。Agent 规划、工具里的 RAG/摘要调用、最终回答共用这一份预算，流式响应结束或失败后才结束其生命周期。CLI 评测继续使用原有显式实验预算。

## 默认上限

在 `rag/.env` 或进程环境中配置，修改后重启应用：

| 配置 | 默认值 | 含义 |
| --- | ---: | --- |
| `REQUEST_TIMEOUT_SECONDS` | 90 | 从进入问答中间件到响应完成的总时间，秒 |
| `REQUEST_MAX_MODEL_CALLS` | 6 | 真正发出的模型 HTTP 请求次数，包含失败的尝试和工具内部调用 |
| `REQUEST_MAX_OUTPUT_TOKENS` | 4096 | 本次问答累计输出额度 |
| `REQUEST_MAX_TOKENS_PER_CALL` | 1024 | 每次模型请求的最大输出额度 |
| `REQUEST_MAX_CONTEXT_BYTES` | 120000 | 累计上下文大小 |
| `REQUEST_MAX_CONTEXT_BYTES_PER_CALL` | 32000 | 单次上下文大小 |

上下文大小是 `messages` 和 `tools` 按固定键序、紧凑 JSON 编码后的 UTF-8 字节数，不是输入 token 数，不含 HTTP headers。实际输入/缓存输入/输出 token 继续通过已有 usage callback 统计。

## 执行链路

1. 纯 ASGI 中间件创建 `RequestBudget`，通过 ContextVar 传给当前请求及其 AnyIO 工作线程。它覆盖普通响应和整个 NDJSON 流，包括模型首 token 之前的等待。
2. 应用启动时创建一组同步/异步 HTTP 连接池，关闭应用时释放。RAG 的共享模型和 Agent 临时模型都使用这组池；请求结束只解绑请求上下文，保留连接池。
3. 受保护的模型 HTTP transport 在每次真正发送前，原子地检查时间、次数和上下文大小，并预留输出额度。发出的 `max_tokens` 不超过单次上限及剩余累计额度，网络各项 timeout 不超过剩余任务时间。HTTP 和 SDK 自动重试均关闭；问答中的检索/生成重试也关闭。预算耗尽不能转入另一个模型回退请求。
4. 只有原始 provider `completion_tokens` 可靠时才按实际用量结算。失败、缺失 usage、流提前关闭都保留整次预留。gzip 等压缩响应先增量解码，再读取 usage；不缓冲完整模型流。`finish_reason=length` 明确作为输出预算失败。
5. Agent 保留原来的四轮限制。每次规划或工具执行前检查容量；规则拆解工具按子问题数估计模型调用上界，留下一次最终回答及其输出额度。工具被跳过时，每个 `tool_call_id` 都有对应的 ToolMessage，随后使用 `tool_choice=none` 收尾。
6. 服务层在下游返回、开始下一个子问题、缓存写入前再次检查。预算失败是粘性的，SDK 包装异常或业务层捕获异常不能让它恢复为成功。

可选微调 router 的 HTTP 尝试也会计数，并按剩余时间限制等待；它不报告可靠 token usage，因此保留整个输出预留。该服务的远端生成参数仍由 router 自己配置，不能把这里的预留理解成远端已执行了 token 截断。

## 失败响应

响应 headers 尚未发送时，普通接口返回 JSON：时间耗尽为 HTTP 504，其他预算耗尽为 HTTP 503。

```json
{"error":{"code":"task_timeout","message":"请求超过执行预算，请缩小问题后重试。"}}
```

流已经开始时保持 HTTP 200，使用原协议输出 `error` 与失败的 `done`。Agent 的 code 位于 `data.code`，`done.data.termination_reason` 为 `failed`；RAG 的 code 位于顶层，`done.status` 为 `failed`。可能的预算原因包括 `task_timeout`、`model_calls_exhausted`、`output_tokens_exhausted`、`context_budget_exhausted`。前端保留已收到的片段并显示错误。

中间件暂存完整行末尾的 `done`，等生成器清理及应用任务正常结束后才发布，避免成功 done 后又追加失败。半条 NDJSON 记录在失败时丢弃，已完成的行仍可增量发送。所有底层发送共用一把锁，防止取消清理与错误响应交错；等待锁也受时间限制。

成功响应的最后发送仍受剩余时间及断连监督。失败时先停止新工作、取消应用等待，再用最多 250 毫秒尽力发送错误。客户端断开或持续不读取时无法保证错误送达，响应可能中断；不会为了发送错误无限等待或再发送一组 headers。请求 ID 与 CORS 仍适用。

## 当前边界与验证

这是一份问答请求的预算。跨请求的每日额度需要共享存储和用户身份，当前未实现。也未增加并发信号量或排队：下一步对应 roadmap A3.3。

停止等待不能强制杀掉已经运行的同步线程或远端模型。Embedding/Qdrant 等已开始的检索仍受原客户端 timeout 约束；请求监督器结束等待，并阻止线程开始后续子问题、模型调用和缓存发布。预算检查也不能撤销已经发出的 Redis 写入或远端计算。连接关闭不代表供应商立刻停止计费。活动工作的名额跟踪和释放属于 A3.4，不能把本次测试解释为已经解决。

离线回归覆盖四条真实路由、生产客户端工厂、真实 ChatOpenAI 加本地 MockTransport、内部 RAG 工具与最终合成、请求隔离、普通 JSON/流式失败、首 token 前超时、流结束清理、迟到线程、缓存写入前检查、压缩 usage、慢客户端和取消发送交错。不需要真实模型、向量库或 Redis。

```bash
uv run --no-sync pytest -q
cd frontend
npm test
npm run lint
npm run build
```
