# 上游调用超时加固（2026-08-08）

## 结论

修复前，所有 LLM 与 embedding 调用**没有任何超时**：底层 httpx 的超时是 `Timeout(timeout=None)`，即无限等待。由于 `/ask`、`/agent/run` 都是同步 `def` 路由、跑在 FastAPI 约 40 线程的线程池里，一次挂死的上游调用会**永久占住一个线程**；累积到线程池耗尽后整个服务不可用，包括健康检查。

这是本仓库当前唯一一个能导致整体不可用的缺陷。现已修复并实测验证。

## 修复前的实测证据

```
LLM  : httpx timeout = Timeout(timeout=None), max_retries = 2
embed: httpx timeout = Timeout(timeout=None)
```

值得注意的是前端已在 2026-08-03 加了 60 秒 stall watchdog：**浏览器 60 秒放弃后，服务端线程仍在无限期等待**。用户重试只会加速线程泄漏。

## 改动

配置项集中在 `rag/src/rag_app/config/config.py`，两个客户端各一处：

| 配置 | 默认值 | 依据 |
|---|---:|---|
| `LLM_TIMEOUT_SECONDS` | 60.0 | `/ask` P95 约 19.5s，Agent 单轮 LLM 调用约 25–30s（2026-08-04 基线），留约 2–3 倍余量 |
| `LLM_MAX_RETRIES` | 1 | SDK 默认 2，最坏耗时是超时的 3 倍；收紧到 1 使单次调用最坏为 2×60s |
| `EMBEDDING_TIMEOUT_SECONDS` | 30.0 | 本机 Ollama，正常在秒级 |

**超时加在单次上游调用上，不是整个请求上。** Agent 一次请求会跑多轮，整体设 60s 会砍掉正常的多步问题。

## 验证

### 1. 传输层确实生效

```
LLM  : httpx timeout = Timeout(timeout=60.0), max_retries = 1
embed: httpx timeout = Timeout(timeout=30.0)
```

### 2. 挂死上游会按时放弃

用一个接受 TCP 连接但永不回复的假上游，设 `timeout=5s, max_retries=1`：

```
gave up after 10.52s -> APITimeoutError
```

10.5s ≈ 2 次尝试 × 5s，与配置一致。**修复前此调用永不返回。**

### 3. 正常请求未受影响

`/ask` 并发 1 与 4 各 4 次请求：P50 4.0s / 4.1s，P95 5.8s / 5.7s，错误率 0。均远低于 60s 超时。

### 4. 回归测试有效

`test_get_client_bounds_the_upstream_wait` 与 `test_get_embeddings_bounds_the_upstream_wait`。已验证：把 `timeout`/`max_retries` 从客户端参数中移除后，测试以 `KeyError: 'timeout'` 失败。

## 残留问题

**应用层重试会与 SDK 重试叠乘。** `ask_service` 自身有 `MAX_GENERATION_RETRY = 1`，与 SDK 的 1 次重试相乘，`/ask` 的生成环节最坏为 2 × 2 × 60s = 240s；Agent 4 轮最坏可到 480s。

修复前这个数字是「无限」，所以现状是明确改善，但**最坏路径仍然偏长**。若要进一步收紧，应先决定重试到底放在哪一层，而不是两层各留一点——这需要先有 request-id 与耗时日志才能判断重试实际发生的频率，因此排在可观测性之后。

## 边界

- 超时值来自本地单机实测，换模型或换网络环境后应重新评估。
- 未验证线程池在长时间高压下的真实回收行为（需要能持续制造挂死上游的环境）。
- Qdrant 客户端的超时未纳入本轮审计。
