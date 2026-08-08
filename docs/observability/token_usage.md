# Token 用量与成本计量

## 解决的问题

改动前项目没有统计过任何 token 用量。「一次 Agent 请求比单次 RAG 贵多少」「答案缩短能省多少」这类问题只能靠猜。

## 实测结论

同一个问题（`对比 Chroma 和 Qdrant，分别适合怎样的 RAG 项目？`）两条路径的真实用量：

| 路径 | LLM 调用 | 输入 | 其中缓存命中 | 输出 | 合计 |
|---|---:|---:|---:|---:|---:|
| `/ask` 单次 RAG | 1 | 1308 | 0 | 282 | **1590** |
| `/agent/run` 多步 | 6 | 6502 | 4065 | 1194 | **7696** |

**Agent 的 token 消耗约为单次 RAG 的 4.8 倍，LLM 调用次数是 6 倍。**

但按计费口径要扣掉缓存命中：Agent 的 6502 输入里有 **4065（62.5%）命中了服务端 prompt 缓存**，实际按全价计费的输入只有 2437。缓存命中的单价通常低一个量级，所以真实成本差距**明显小于 4.8 倍**——不区分开算会显著高估。

流式路径同样有数据（`/agent/run/stream`，3 次调用 / 3366 tokens），这依赖下面那条 `stream_usage` 的修复。

## 实现

| 文件 | 作用 |
|---|---|
| `rag/src/rag_app/infrastructure/token_usage.py` | `RequestUsage` 计数器、`TokenUsageCallback`、成本计算 |
| `rag/src/rag_app/infrastructure/llm_client.py` | 开启 `stream_usage`，挂载回调 |
| `rag/src/rag_app/infrastructure/request_context.py` | 请求开始建计数器，结束打汇总日志 |
| `rag/src/rag_app/config/config.py` | 每百万 token 单价（输入 / 缓存输入 / 输出） |

### 三个关键决定

**1. 用 callback，不改调用链。**
`answer_generator.py` 的链末端是 `StrOutputParser`，会把 `AIMessage` 丢掉。实测确认回调仍能拿到 `usage_metadata`，所以现有调用代码一行未改，且自动覆盖全部调用点（RAG 生成、Agent 工具选择、摘要、收尾合成）。

**2. 必须显式开 `stream_usage=True`。**
langchain 只在使用官方 OpenAI 地址时才默认开启；本项目走 Moonshot 的 base_url，默认是关的。实测：不开时流式路径的 usage 为**空列表**。而流式是唯一的用户可见路径——不修的话统计会系统性偏低，漏掉的正是最贵的部分。

**3. 计数器是「共享可变对象」，不是重新 set。**
`/ask`、`/agent/run` 都是同步路由，跑在 AnyIO worker thread 里，而计数器在异步中间件中创建。**子线程里 `contextvar.set()` 中间件看不到**，所以只能在中间件建好对象、各处只修改它的字段。已实测确认：改成子线程重新 set 后，汇总日志完全消失。

### 价格默认为 0

单价按「每百万 token」配置，默认全为 0。**token 数永远统计，金额只在配置单价后才产出。** 不在代码里写死可能过期的价格，避免拿一个错的成本数字去对外引用。

配置项：`LLM_INPUT_PRICE_PER_MILLION`、`LLM_CACHED_INPUT_PRICE_PER_MILLION`、`LLM_OUTPUT_PRICE_PER_MILLION`。

## 日志形态

与 request-id 共用同一条链路，`grep <id>` 可同时看到耗时与用量：

```
[request_id=cost-agent-1] ask.retrieval  completed duration_seconds=1.15
[request_id=cost-agent-1] ask.generation completed duration_seconds=4.15
...
[request_id=cost-agent-1] usage.request llm_calls=6 input=6502 cached_input=4065 output=1194 total=7696 cost=0.000000
```

## 测试

`rag/tests/infrastructure/test_token_usage.py`（7 条）+ `test_llm_client.py` 新增 1 条：

- **线程池里记录的用量能被中间件读到**——已验证错误实现会让日志消失
- 缓存输入单独计价，不与普通输入重复计算
- 未配价格时 `cost() == 0` 但 token 照常统计
- 多次调用累加成一条汇总
- 请求外调用回调不报错也不产生数据
- `get_client()` 带 `stream_usage=True` 与回调

全量：Python 305 passed。

## 尚未做的

- 没有 `/metrics` 端点，只能 grep 日志，看不到聚合趋势。
- 未按模型区分价格表（当前是全局单价，换模型需改配置）。
- 未统计 embedding 的用量（Ollama 本地运行，无外部计费）。
- 未做预算上限或超支熔断——那属于 E-4，且在没有真实流量前无法确定阈值。**先测量，再谈治理。**
