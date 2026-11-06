# 问答请求预算与并发控制

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
| `REQUEST_MAX_CONCURRENT` | 4 | 当前应用进程最多同时执行的问答任务 |
| `REQUEST_MAX_WAITING` | 16 | 等待执行名额的请求上限；0 表示满额立即拒绝 |
| `REQUEST_QUEUE_TIMEOUT_SECONDS` | 5 | 等待执行名额的最长时间，秒 |

上下文大小是 `messages` 和 `tools` 按固定键序、紧凑 JSON 编码后的 UTF-8 字节数，不是输入 token 数，不含 HTTP headers。实际输入/缓存输入/输出 token 继续通过已有 usage callback 统计。

## 执行链路

1. 纯 ASGI 中间件创建 `RequestBudget`，通过 ContextVar 传给当前请求及其 AnyIO 工作线程。它覆盖普通响应和整个 NDJSON 流，包括模型首 token 之前的等待。
2. 应用启动时创建一组同步/异步 HTTP 连接池，关闭应用时释放。RAG 的共享模型和 Agent 临时模型都使用这组池；请求结束只解绑请求上下文，保留连接池。
3. 受保护的模型 HTTP transport 在每次真正发送前，原子地检查时间、次数和上下文大小，并预留输出额度。发出的 `max_tokens` 不超过单次上限及剩余累计额度，网络各项 timeout 不超过剩余任务时间。HTTP 和 SDK 自动重试均关闭；问答中的检索/生成重试也关闭。预算耗尽不能转入另一个模型回退请求。
4. 只有原始 provider `completion_tokens` 可靠时才按实际用量结算。失败、缺失 usage、流提前关闭都保留整次预留。gzip 等压缩响应先增量解码，再读取 usage；不缓冲完整模型流。`finish_reason=length` 明确作为输出预算失败。
5. Agent 保留原来的四轮限制。每次规划或工具执行前检查容量；规则拆解工具按子问题数估计模型调用上界，留下一次最终回答及其输出额度。工具被跳过时，每个 `tool_call_id` 都有对应的 ToolMessage，随后使用 `tool_choice=none` 收尾。
6. 服务层在下游返回、开始下一个子问题、缓存写入前再次检查。预算失败是粘性的，SDK 包装异常或业务层捕获异常不能让它恢复为成功。

## 并发名额与排队

每个 FastAPI 应用在 lifespan 启动时创建独立的并发限制器。四个问答入口在参数校验后、业务执行和流式 headers 之前申请名额；无效请求仍返回 422。排队期间不执行检索、模型或工具，等待数量有界；总请求预算已经开始计时，排队消耗同一份预算。

默认最多执行 4 个任务、等待 16 个；等待超过 5 秒返回 HTTP 503 `queue_timeout`，等待队列已满返回 HTTP 503 `queue_full`。如果请求总时间先用完，返回 504 `task_timeout`。这些错误在流尚未开始时使用前述 JSON 格式，前端显示具体消息。排队断连会取消名额申请并移除等待者。

一个 Agent 问答只占一个名额，内部 RAG 和摘要工具共用名额与预算。名额覆盖应用执行、最终响应发送和生成器清理。共享连接池独立于请求名额，应用关闭时释放；应用关闭后拒绝新任务，返回 503 `service_unavailable`，下一次 lifespan 使用全新限制器。

这里限制的是当前应用进程的问答工作，不是每分钟请求次数。多个 worker 各有自己的限制：例如 4 个 worker、每个上限 4，合计可执行 16 个问答。分别部署的 RAG 和 Agent 应用也各有自己的限制；没有跨进程、跨服务的统一额度或持久化任务队列。

## 取消后何时归还名额

HTTP 请求结束不代表工作线程已经退出。每个请求的 `RequestWork` 分别记录 app task/清理是否结束、响应监督是否结束、实际 worker 数量。只有三项都完成才归还名额，而且只归还一次。

两个 JSON 入口的同步业务、RAG 同步流的生产线程、Agent 异步循环中的同步工具，都通过显式 worker 适配器跟踪。取消 HTTP 等待不会取消这份实际工作记录。尚未启动的迟到 worker 会先检查已终止的预算，避免启动新的业务调用。

RAG 流由一个受跟踪的线程增量生产，内存通道只缓冲一个条目。取消时关闭接收端，解除生产线程的发送阻塞；迭代器在原生产线程内关闭，避免另一个线程关闭正在执行的 generator。模型或 iterator 清理仍阻塞时继续占有名额。

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

这是一份问答请求的预算，并配合应用进程内的并发控制。跨请求的每日额度需要共享存储和用户身份，当前未实现。

停止等待不能强制杀掉已经运行的同步线程或远端模型。Embedding/Qdrant 等已开始的检索仍受原客户端 timeout 约束；请求监督器结束等待，并阻止线程开始后续子问题、模型调用和缓存发布。预算检查也不能撤销已经发出的 Redis 写入或远端计算。

A3.4 已覆盖本地 worker 和应用清理的名额跟踪；无法观察供应商在连接关闭后是否仍计算，也不能保证其立即停止计费，因此不声称已经限制了供应商内部并发。应用关闭会关闭准入和本地连接，不无限等待线程退出；这次没有新增持久任务恢复或完整的优雅停机机制。

离线回归覆盖四条真实路由、生产客户端工厂、真实 ChatOpenAI 加本地 MockTransport、内部 RAG 工具与最终合成、请求隔离、普通 JSON/流式失败、首 token 前超时、流结束清理、迟到线程、缓存写入前检查、压缩 usage、慢客户端和取消发送交错。不需要真实模型、向量库或 Redis。

2026-11-06：完整 Python 回归 518 项、前端 28 项，以及 lint/default production build 通过。新增测试在四条真实路由分别发起 20 个模拟请求，验证最多执行 4 个实际 worker；另覆盖队满、排队超时、总时限包含排队、排队断连、后台线程未退出时保留名额、响应发送/清理未结束时保留名额、关闭与重启隔离。模拟结果验证控制逻辑，不代表真实模型吞吐量。

```bash
uv run --no-sync pytest -q
cd frontend
npm test
npm run lint
npm run build
```
