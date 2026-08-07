# Redis 精确答案缓存收益报告（2026-08-05）

## 目标

验证同步 RAG 问答使用 Redis Cache-Aside 后，重复问题能够跳过 Qdrant 检索与 LLM 生成，同时保持索引更新后的失效能力和 Redis 故障时的可用性。

## 配置

- Redis：`redis:7.4-alpine`，AOF，`128mb`，`volatile-lru`
- TTL：3600 秒
- 缓存范围：同步 `ask_question()`；`/ask/stream` 不缓存
- 问题：`LangChain 和 LlamaIndex 分别适合做什么？`
- 重复次数：5
- Qdrant collection：`documents`

运行命令：

```bash
uv run python -m rag_app.scripts.benchmark_answer_cache \
  --base-url http://127.0.0.1:8001 \
  --repeat 5 \
  --output rag/experiments/reports/cache/redis_exact_cache_2026-08-05.json
```

## 结果

| 轮次 | 缓存状态 | HTTP 延迟 | 来源数 |
| ---: | --- | ---: | ---: |
| 1 | miss | 5915.14 ms | 7 |
| 2 | hit | 4.41 ms | 7 |
| 3 | hit | 3.46 ms | 7 |
| 4 | hit | 3.53 ms | 7 |
| 5 | hit | 3.25 ms | 7 |

- 命中率：80%（4/5）
- miss P50：5915.14 ms
- hit P50：3.495 ms
- 重复请求 P50 降幅：99.94%
- 五次响应的 `answer + sources` SHA-256 完全一致。
- 首次请求只执行一次 Qdrant 检索和一次 LLM 生成；后四次直接返回相同答案与 7 个来源。

原始结果见 `redis_exact_cache_2026-08-05.json`。

## 正确性与故障演练

- 两个独立 Redis client 对同一 key 完成跨客户端写入与命中。
- 索引版本从 1 递增到 2 后，旧版本答案立即 miss。
- 答案 key 带 TTL；索引版本 key 不带 TTL，避免被 `volatile-lru` 淘汰。
- 生成期间索引版本变化时，旧索引答案不会写入新版本命名空间。
- 停止 Redis 后真实调用 `/ask`：HTTP 200，trace 为 `answer_cache=unavailable`，答案正常返回；恢复后 `PING` 为 `PONG`。

## 边界

- 当前仅做精确缓存，不做语义缓存。
- 流式回答不缓存，避免伪造 token 流回放语义。
- 未实现分布式 single-flight；多个并发的相同冷请求仍可能同时调用 LLM。
- 索引已成功修改但 Redis 版本递增失败时，旧答案最多保留到 TTL 到期；日志会记录失效失败。生产化下一步应给索引更新增加更严格的协调协议。
