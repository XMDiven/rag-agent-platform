# 项目精进作战地图（Living Build Playbook）

> 读者：本仓库拥有者（大三，目标 AI 应用开发 / LLM Engineering 实习）。
> 用途：扎根**真实代码**，让 AI 一步一步驱动你精进项目，并把项目**维持**在「实习有竞争力」的水平。
> 这是一份**活文档**：项目每前进一步，就回来更新「§2 进度看板」和「§4 现状评估」。它不是写完就放着的报告。
> 写法约定：正文中文 + 英文技术术语/库名。铁律：先看代码再下结论，每条判断都指向具体文件。
> 当前阶段：P0/P1/P2 看板已完成，进入「§8 维持竞争力的循环」——本文件只谈代码与工程，不写简历表述。

---

## ★ 给 AI 的使用协议（每次进入仓库先读这一节）

你是协助本仓库拥有者精进项目、维持实习竞争力的**工程教练**。每次按以下循环工作，不要一次性堆完所有事：

1. 读「§1 竞争力北极星」确认终点，读「§2 进度看板」确认现在走到哪。
2. 取进度看板里**第一个未勾选**的任务。**一次只做一个**，不跳步、不并行、不提前做加分项。
3. 动手前先讲清：成功标准、要改哪些文件、不做什么、需要的环境前置。
4. 按「§6 路线图」执行对应任务；遇到标「⚠ 人工决策点」的，**先停下问用户**，不要替他决定。
5. 跑该任务的**验收命令**自证完成；无法用命令验证的，明确说「需人工确认」并说明怎么确认。
6. 完成后做三件事：① 把看板该项 `[ ]` 改 `[x]` 并填日期；② 把「§4 现状评估」里被修复的条目划掉或降档；③ 汇报（改了哪些文件 / 跑了什么命令 / 是否通过 / 留下什么证据 / 下一步是什么）。
7. 全程遵守「§7 红线」；拿不准就回到「§3 根本判断」的**深度优先**原则。

---

## §1 竞争力北极星（什么叫「有竞争力」）

### 1a 目标岗位考察什么（按与本项目相关度排序）

AI 应用开发 / LLM Engineering 实习通常考察：

1. **RAG 全链路**：切分、embedding、向量检索、召回质量（rerank/hybrid/filter）、引用与防幻觉。 ← 主战场
2. **LLM 编排**：prompt 设计、结构化输出、function/tool calling、多步 agent/workflow。 ← 第二战场，且最薄
3. **工程化**：服务化（FastAPI）、配置/密钥管理、错误处理、日志/可观测性、并发正确性。 ← 有底子，有欠账
4. **质量保障**：测试、eval、对 LLM 输出的回归验证。 ← 相对强项
5. **部署与成本**：Docker、环境隔离、token/延迟/成本意识。 ← 已有 RAG/Agent Dockerfile、Qdrant compose、latency/eval 报告；后续可补一键编排与容器级 smoke test
6. **数据层**：关系库 / 缓存 / 向量库合理使用。 ← 仅 Qdrant，其余非必需
7. **能讲清工程权衡**：为什么这么选、瓶颈在哪、如何取舍。 ← 取决于你能否复述本文件所有判断

### 1b 达标线（项目达到这些，才算「有竞争力」）

项目**同时**满足以下四条，即跨过实习竞争力门槛：

1. **真 Agent**：一个基于 native function calling 的工具编排能跑通且有测试（不再是关键词 if/else）。
2. **有数据的优化**：至少一次检索质量或延迟优化，有定义清晰的指标和 before/after 数据。
3. **生产像**：async 正确（无阻塞事件循环）、有结构化日志、客户端复用。
4. **可复现**：一份从零跑通的端到端 demo + 全绿测试 + 新鲜的 eval 报告。

达不到 1–4 之前，**不要横向加新功能**。

### 1c 竞争力是动态的，要「维持」不是「一次达标」

跨过门槛只是起点。岗位要求会变、你会投更多家——所以这份文档要持续运转，见「§8 维持竞争力的循环」。

---

## §2 进度看板（唯一状态源）

> 维护规则：完成一步把 `[ ]` 改 `[x]` 并填日期；不提前勾选；前置门 P-1 必须先过，地基 P0 必须先于 P1/P2。

**前置门（影响所有「需 live stack」的任务）**

| 门 | 内容 | 状态 |
|---|---|---|
| P-1 | `.env` 配齐（`LLM_BASE_URL`/`LLM_MODEL_ID`/`MOONSHOT_API_KEY`/`QDRANT_URL`/`QDRANT_COLLECTION`）+ `docker compose up -d qdrant` + `build_index` 已建索引 | [x] 2026-06-04 |

**任务看板**

| # | 任务 | 档位 | 需 live stack | 状态 | 完成日期 |
|---|---|---|---|---|---|
| 0.1 | 端到端 demo 落到 `docs/demo/` | 地基 | 是 | [x] | 2026-06-04 |
| 0.2 | 修 async 错配 + 加结构化日志 | 地基 | 否 | [x] | 2026-06-04 |
| 0.3 | 统一流式/非流式检索重试 + 客户端复用 | 地基 | 部分 | [x] | 2026-06-04 |
| 1.1 | 真实 Function Calling 的 Agent | 深度 | 是 | [x] | 2026-06-04 |
| 1.2 | 可量化的检索质量优化 | 深度 | 是 | [x] | 2026-06-07 |
| 1.3 | 用真工具替换 summary 桩（web_search 经决策不做） | 深度 | 是 | [x] | 2026-06-07 |
| 2.1 | 扩评测样本并留运行记录 | 规模 | 是 | [x] | 2026-06-07 |
| 2.2 | app 加 Dockerfile + pydantic-settings | 规模 | 否 | [x] | 2026-06-09 |

---

## §3 根本判断（先看这里，再看路线）

技术方向**没有根本错误**：`FastAPI + LangChain + Qdrant + OpenAI-compatible LLM` 主流且适配岗位，分层清晰，有测试有 CI，不需推倒重来。

但有一个**结构性短板**：**被当头牌的「Agent」恰恰是最薄的一层。** `planner.py` 是关键词 if/else 路由，`summary_tool` 是字符串截断（`summary.py:1-16`），`question_decompose` 是单一 `"分别"` 分隔符的字符串切分（`question_decompose.py:39-66`）。对 LLM Engineering 岗，这层是考察重点，却是 demo 级。

**总策略：停止横向加功能，纵向把一条线做深。** 五个浅 demo 不如一条「真 function calling + 测试 + trace」的工具链做到接近生产质量。深度 > 广度——这是区分「调过 API 的人」和「能做 LLM 工程的人」的关键。

---

## §4 现状评估（三档 + 文件证据）

> AI 修复某问题后，回来把对应条目划掉或降档，保持这张表反映**当前**真实状态。括号里的「→任务 X」是修它的任务。

### A 档：已实现、接近可用质量

- **清晰分层**：`app/routers → services → retrieval|generation|ingestion|evaluation → infrastructure → config|schemas`，职责分离干净。最强工程信号。
- **真实测试 + CI**：`rag/tests` 119 个 test 函数、`agent/tests` 53 个；`.github/workflows/tests.yml` 在 push/PR 跑两套 pytest。（agent 2026-06-14 实跑 53 passed、离线全绿；rag 以 2026-06-09 实跑 119 passed 为准；简历引用以最近一次实跑为准。）
- **安全意识**：path-traversal 防护——`documents.py:17-35,52-62` 用 `Path(filename).name`；`prompt_eval_service.py:31-37` 校验 report 父目录。
- **结构化 trace 贯穿全链路**：`ask_service.py:74-83,200-342`、`service.py:52-75`。
- **检索 + 生成有限重试**：`ask_service.py:128-197`、`264-342`，失败落 `FALLBACK_ANSWER`。
- **评估闭环（真亮点）**：LLM-as-Judge + Pydantic，`judge_schema.py:9-31` 用 `model_validator` 强制 `overall_pass` 由四维分数推导，杜绝模型自评注水；A/B 与报告持久化（`prompt_eval_service.py`）。
- **真·多步 Agent loop（2026-06-14 新增，agent 线的纵向加深）**：`agent/src/agent_app/orchestration/loop.py` 用 `bind_tools`+`tool_choice="auto"` 跑多轮 ReAct——模型自主选工具→`run_tool` 执行→结果/失败回灌 `ToolMessage`→模型再决策，三种终止语义（final_answer/max_steps/failed），耗尽预算时去工具强制收尾。`service.run_agent` 默认走 loop，loop 异常或空问题降级到单步 `run_agent_once`（`service.py:114-201`）；`executor.run_tool` 按工具名统一派发；`/agent/run` 已接线并改同步 handler（不阻塞事件循环）。离线 fake-LLM 覆盖 loop 成功 / fallback / 工具失败恢复路径（`agent/tests/conftest.py`、`test_service.py`、`test_run_api.py`）。

### B 档：原型级（能跑，缺边界 / 可观测 / 一致性）

- ~~**async/sync 错配（生产隐患）**：`/ask`、`/ingest` 是 `async def` 却调阻塞同步 I/O（`ask.py:24-27`、`documents.py:86-92`），高并发阻塞事件循环。（→任务 0.2）~~ 已于 2026-06-04 修复：阻塞路由改为同步 handler，并用测试固定。
- ~~**流式与非流式不一致**：`stream_ask_question` 直接 `retriever.invoke()`，**无**重试（`ask_service.py:398-399` vs `128-197`）。（→任务 0.3）~~ 已于 2026-06-04 修复：`/ask/stream` 复用 `retrieve_documents_with_retry`，检索失败进入 retry/fallback trace，并有测试覆盖。
- ~~**零日志**：全仓库 grep 不到 `logging`，只有 in-band trace。（→任务 0.2）~~ 已于 2026-06-04 修复：RAG app 配置标准库 `logging`，ask/ingest 服务记录耗时与错误；request-id 可作为后续可观测性增强。
- ~~**客户端无复用**：`get_client`/`get_vector_store`/`get_retriever` 每请求重建（`llm_client.py:9-29`、`vector_store.py:15-27`、`retriever.py:5-14`）。（→任务 0.3）~~ 已于 2026-06-04 修复：FastAPI lifespan 创建 `AppResources`，`/ask`、`/ask/stream`、`/documents/ingest` 复用启动期 LLM client/vector store；请求级 retriever 仍按 `top_k` 轻量创建。
- ~~**配置半集中**：`CHUNK_SIZE/CHUNK_OVERLAP` 硬编码 `config.py:11-12`；`load_dotenv` 三处重复；`vector_store.py:16` 直接 `os.getenv` 绕过 config。（→任务 2.2）~~ 已于 2026-06-09 修复：`Settings` 统一读取 `.env`/环境变量，LLM、embedding、Qdrant、chunk 与 retrieval 配置从 `config.settings` 进入代码；`load_dotenv`/散落 `os.getenv` 已移除。
- **无 lint/type 检查**：有类型标注但无 mypy/ruff，dev 依赖只有 pytest。

### C 档：声称有、实际很薄（命名与实现风险）

- ~~**`summary_tool` 不是摘要**：`summary.py` 是 `text[:200]` 截断。（→任务 1.3）~~ 已于 2026-06-07 改为真 LLM 摘要（`agent/src/agent_app/tools/summary.py`：调 LLM、空输出兜底、长度上限；`executor.py` 像 retrieval 一样捕获失败落 `status=failed` 进 trace），含成功/空/截断/失败单测。`web_search_tool` 经用户决策不做。
- **`question_decompose` 极脆**：只有 `"分别"` 一个可靠切分模式，否则原样返回（`question_decompose.py:62-66`）。（→任务 1.1 改造后弱化）
- ~~**planner 是关键词 if/else**：`planner.py:13-35`，无 LLM、无 function calling。（→任务 1.1）~~ 已于 2026-06-04 修复：`plan_tool` 走 LLM native function calling（`tool_selector.py` 用 `bind_tools` + `tool_choice="auto"`）选工具并填参，规则式 `plan_tool_by_rules` 降为 fallback；真实模型已验证能返回 `tool_calls`，`/agent/run` trace 能看到 `tool_args`。
- **chunking 只有两种**：Markdown=header+Recursive、PDF=Recursive；**代码中未发现** fixed-size 抽象或 semantic chunker。
- ~~**检索只是 plain similarity top-k**：`retriever.py:9-12`，无 rerank/hybrid/MMR/filter。（→任务 1.2）~~ 已于 2026-06-07 处理：检索策略改为配置控制（`RETRIEVAL_SEARCH_TYPE`），新增 MMR 支持并用扩到 27 条（含 3 条实测难例）的 golden set 评测；结果显示 MMR 在每个 λ 都回归 MRR（similarity 0.901 vs mmr 0.892@λ0.3，λ≥0.5 连 hit_rate/coverage 也降）、唯一收益是多样性且 judge 无提升，故**默认保留 similarity**，MMR 作为可配置 opt-in。详见 `rag/experiments/retrieval_baseline_2026-06-05.md`。
- ~~**评测样本极小**：11 golden cases、2 prompt 版本、3 个 judge run。（→任务 2.1）~~ 已于 2026-06-07 扩充并留运行记录：retrieval golden set 扩到 27 条（含 3 条实测 hard cases），answer-level judge run `rag/experiments/judge_runs/20260607-201409.json` 覆盖 27 条并 27/27 passed；平均耗时 answer 35.67s、judge 44.24s、total 79.91s。

### 最严重的工程问题（按对生产质量影响排序）
1. ~~async 路由跑阻塞 I/O（并发正确性）→任务 0.2~~ 已修复，2026-06-04
2. ~~完全没有日志/可观测性（不可运维）→任务 0.2~~ 基础 logging 已补，2026-06-04
3. ~~流式与非流式健壮性不一致→任务 0.3~~ 已修复，2026-06-04
4. ~~核心 Agent 逻辑是字符串规则（岗位短板）→任务 1.1~~ 已修复，2026-06-04：LLM function calling + 规则 fallback 已落地，真实模型验证通过，trace 已记录 `tool_args`
5. ~~客户端每请求重建 / 配置分散~~ 客户端复用已通过 lifespan resources 修复，2026-06-04；配置集中已通过 `pydantic-settings` 修复，2026-06-09

---

## §5 差距分析

**已能证明（可指着代码讲）**：端到端 RAG（ingest→chunk→检索→grounded generation→来源引用→trace）；评估工程化（eval + LLM-as-Judge + A/B + 报告）；基础工程（分层、测试、CI、防护、有限重试）。

**还缺（岗位会问、当前答不上）**：真 function/tool calling 与 LLM 驱动编排；检索质量优化手段；可观测性与并发正确性；可量化可复现的结论。

**最值得补的 3 件（深度优先）**：
1. 把 Agent 做成真的（任务 1.1）——补最薄一环，复用现有 schema，性价比最高。
2. 把 RAG 服务做「生产像」（任务 0.2 + 0.3）——最便宜的可信度提升。
3. 做一次可量化的检索优化（任务 1.2）——把「召回质量」从口号变数字。

---

## §6 优先级路线图（每个任务都可验收）

> 工作量：S≈半天，M≈1–2 天，L≈3–5 天。验收命令默认在对应子项目目录下用 `conda run -n AI_DEV` 执行。

### P0 — 可信度地基（先做）

**任务 0.1 · 端到端 demo 落到 `docs/demo/`（S，需 live stack）**
- 做什么：起 RAG+Agent 服务，真实 `curl` 跑 upload→ingest→ask→ask/stream→agent/run，把命令与真实响应存成 `docs/demo/end_to_end.md`，写明环境前提。
- 环境依赖：P-1 全部就绪。
- 验收命令：`uvicorn rag_app.app.main:app --port 8002` 与 `uvicorn agent_app.app.main:app --port 8001` 起服务后按文档 curl。
- 完成信号：照 `docs/demo/end_to_end.md` 重跑能复现同形状响应；README 链接到它。（需人工确认响应合理）

**任务 0.2 · 修 async 错配 + 加结构化日志（M，纯代码）**
- 做什么：阻塞路由改对（路由改 `def` 让 FastAPI 丢线程池，或 `await run_in_threadpool(...)`）；引入 `logging`，在 service 边界与每个外部调用（Qdrant/LLM）打 request-id + 耗时 + 错误。
- 改哪里：`ask.py`、`documents.py`、`ask_service.py`、`ingest_service.py`、新增 logging 配置。
- 验收命令：`cd rag && conda run -n AI_DEV pytest tests/ -q`（应仍全绿）。
- 完成信号：测试全绿 + `grep -rn "logging" rag/src` 非空 + 不再有 `async def` 路由直接调阻塞同步函数。
- 不做：不重写业务逻辑；不引日志框架（标准库 `logging` 足够）。

**任务 0.3 · 统一流式/非流式检索重试 + 客户端复用（S–M，部分需 live stack）**
- 做什么：把 `retrieve_documents_with_retry` 用进 `stream_ask_question`；用 FastAPI lifespan/依赖注入缓存 LLM client 与 vector store。
- 改哪里：`ask_service.py`、`app/main.py`、`llm_client.py`/`vector_store.py`（暴露可缓存入口）。
- 验收命令：`cd rag && conda run -n AI_DEV pytest tests/ -q`。
- 完成信号：流式路径检索失败也走 retry/fallback 并入 trace；客户端在进程内单例复用。

### P1 — 能力深度（一次只推一条主线，做透再开下一条）

**任务 1.1 · 真实 Function Calling 的 Agent（L，需 live stack）⚠ 这是 P1 的默认起点**
- 做什么：复用 `registry.py` 的 `input_schema` 作为 tool/function parameters，接 LLM native tool calling 让模型选工具并填参；规则式 planner 降级为 fallback；trace 记录模型的 tool-call 决策。
- ⚠ 人工决策点 / 前置确认：先确认 `LLM_MODEL_ID`（Kimi/Moonshot 兼容接口）**支持 tool calling**；若不支持，退而用 JSON-mode 结构化输出让模型选工具——这条要先问用户走哪条。
- 改哪里：`planner.py`、`executor.py`、`service.py`、新增 function-calling 适配。
- 验收命令：`cd agent && conda run -n AI_DEV pytest tests/ -q` + 新增 function-calling/fallback 单测。
- 完成信号：`/agent/run` 的 trace 能看到「模型选了哪个工具、参数是什么」；两条路径都有测试。
- 不做：不引入 AutoGen；不删规则式 planner（它是 fallback）。
- 完成记录（2026-06-04）：已实现 `tool_selector.select_tool_with_llm`（`bind_tools` + `tool_choice="auto"`，单工具取首个 tool_call，未知工具抛错）+ `planner.plan_tool`（LLM 优先、异常 fallback 规则并记 warning）+ `service.run_agent` 透传并在 trace 记录 `tool_args`；真实 HTTP `/agent/run` 返回 `selected_tool=retrieval_tool`，`plan_trace.detail.tool_args={"question": "LangChain 是什么？"}`；`cd agent && conda run -n AI_DEV pytest tests/ -q` → 45 passed。

**任务 1.2 · 可量化的检索质量优化（M–L，需 live stack）**
- 做什么：加一种召回增强（reranking / metadata filter / MMR 选一）；先定义指标（source hit rate / all-source hit rate），固定 baseline 与 case set，量「改进前→改进后」，扩 golden set，写报告并标注样本量。
- 改哪里：`retriever.py`/新增 rerank 模块、`retrieval_eval_cases.json`、`experiments/` 报告。
- 验收命令：`cd rag && conda run -n AI_DEV python -m rag_app.scripts.evaluate_retrieval`（前后各跑一次对比）。
- 完成信号：`experiments/` 有含指标定义、公式、前后对比、样本量的报告。
- 不做：不把 10/11→11/11 硬写成「20%」。
- 完成记录（2026-06-06）：已跑当前 plain similarity `top_k=7` baseline，记录到 `rag/experiments/retrieval_baseline_2026-06-05.md`；已补 first-hit rank / MRR / expected source coverage / unique source 指标，并让 `evaluate_retrieval` 与 `evaluate_answers_with_judge` 支持显式 `--search-type/--top-k/--fetch-k/--lambda-mult` 实验。`top_k=3` 对比为 10/11 passed，coverage 从 1.000 降到 0.955，结论是保留 `top_k=7`。2026-06-06 曾把默认切到配置控制的 `mmr λ=0.3`（11 条上 11/11、mrr=0.909、unique 2.545→3.818、judge 11/11）。但 2026-06-07 把 golden set 扩到 27 条（含 3 条实测难例）后，有区分度的评测显示 MMR 在每个 λ 都回归 MRR（similarity 0.901 vs mmr 0.892@λ0.3，λ≥0.5 连 hit_rate/coverage 也降）、唯一收益是多样性而 judge 无提升，故**默认已回退 similarity**（commit 7462509），MMR 保留为 opt-in。详见 `rag/experiments/retrieval_baseline_2026-06-05.md` 的 Revision 段。

**任务 1.3 · 用真工具替换 demo 桩（M，需 live stack）**
- 做什么：`summary_tool` 换成 LLM 摘要（带长度/失败处理）；加**受控** `web_search_tool`（明确 API 边界、超时、失败处理、mock 测试）。
- 验收命令：`cd agent && conda run -n AI_DEV pytest tests/ -q` + 每工具的成功/失败单测。
- 完成信号：trace 能看到工具名与状态；失败路径有测试覆盖。
- 不做：没有失败处理和测试，不接外部 API。
- 完成记录（2026-06-07）：`summary_tool` 已换成真 LLM 摘要（`agent/src/agent_app/tools/summary.py`，空输出兜底 + 长度上限 + 失败 raise；`executor` 失败落 `status=failed` 进 trace），`cd agent && conda run -n AI_DEV pytest tests/ -q` → 47 passed。**`web_search_tool` 经用户决策不做**，故 1.3 收口为「仅真 summary 工具」。

### P2 — 规模与收尾

**任务 2.1 · 扩评测样本并留运行记录（M，需 live stack）**
- 做什么：真实扩 golden set / prompt 对比组，保存每次 judge run，统计组数/样本数/耗时。
- 验收命令：`cd rag && conda run -n AI_DEV python -m rag_app.scripts.evaluate_answers_with_judge`。
- 完成信号：新增 case 有来源说明 + `experiments/judge_runs/` 新记录。
- 完成记录（2026-06-07）：`retrieval_eval_cases.json` 已从 11 扩到 27 条，并在 `rag/experiments/retrieval_baseline_2026-06-05.md` 记录 similarity vs MMR λ sweep；answer-level judge 最新 run `rag/experiments/judge_runs/20260607-201409.json` 覆盖 27 条 default cases，`similarity top_k=7` 下 27/27 passed，平均 answer=35.67s、judge=44.24s、total=79.91s。

**任务 2.2 · app 加 Dockerfile + pydantic-settings（S–M，纯代码）**
- 做什么：为 RAG/Agent 各加 Dockerfile；配置集中到 `pydantic-settings`（替代散落 `os.getenv` + 重复 `load_dotenv`）。
- 验收命令：`docker build` 两个服务镜像 + `pytest tests/ -q` 仍绿。
- 完成信号：`docker build` 可成功；配置从单一 Settings 对象读取。
- 完成记录（2026-06-09）：已新增 `rag/Dockerfile` 与 `agent/Dockerfile`；端口约定为 RAG `8002`、Agent `8001`；RAG 配置集中到 `rag_app.config.config.Settings`，LLM、embedding、Qdrant、chunk、retrieval 参数统一从 `settings` 读取；Agent 通过 `agent-rag` 显式依赖本地 RAG 包。验证命令已跑：`docker build -t rag-app ./rag`、`docker build -f agent/Dockerfile -t agent-app .`、两个镜像内 `from ...app.main import app`、`cd agent && conda run --no-capture-output -n AI_DEV pytest tests -q` → 47 passed。对应提交：`ee84fda build: add agent docker image`。

---

## §7 反模式与红线（明确不要做）

- **不要为了好看加一堆浅 demo。** 五个半成品不如一个有 function calling + 测试 + trace 的真工具链。深度 > 广度。
- **不要在 README / docs / demo 里声称代码没有的能力。** 当前**代码中不存在**、未做出来前不写进任何文档：`Semantic chunking`、`三种 Chunk 策略`、`Function Calling`、`AutoGen`、`网络搜索工具`、严格定义的`命中率`与`延迟`指标。
- **不要过度设计 / 过早抽象。** 不为「平台感」做复杂前端；不为单一用途造插件系统；config 用 `pydantic-settings` 即可，别上配置中心。
- **不要把临时写法伪装成架构。** `summary_tool` 截断、`question_decompose` 单模式切分——要么做真，要么命名和文档如实降级。
- **不要引入用不上的中间件。** 没有真实并发/持久化需求前，不上 Redis、消息队列、关系库。先把 Qdrant 用好。
- **不要在错误方向上做局部优化。** 每加一个功能先问：它补齐哪项岗位能力？留下什么可验证证据？有没有更小的做法？

---

## §8 维持竞争力的循环（跨过门槛后，让项目不退化）

竞争力会随时间和岗位要求衰减。**触发时机**：每完成一个 P 阶段、每次准备投递前、或每月一次，跑一遍这个循环：

1. **证据保鲜**：重跑测试 + `run_eval` + `benchmark_latency` + demo，确认 §4 A 档能力没回归，报告是最新的。简历投递引用的任何数字，都以最近一次实跑为准。
2. **重估现状**：重读 §4，把已修问题降档；把新出现的原型级问题补进 §2 看板。
3. **跟住岗位**：看几份当前 AI 应用 / LLM 实习 JD 在要什么（新框架、新评测标准、新 agent 范式），把新增 gap 写进 §5。
4. **继续深挖一条线**：roadmap 做完后，挑已选主线的对立面（做了 1.1 就做 1.2），或选一个**全新垂直能力**做透——而不是回头铺浅功能。
5. **守住原则**：维持竞争力 = 证据保鲜 + 持续深挖 + 跟住岗位，**不是**无限堆功能。每轮只深化一处。

---

## 附：本次实际读取的关键文件（供核对）

依赖与工程化：`rag/pyproject.toml`、`rag/requirements.in`、`agent/pyproject.toml`、`.github/workflows/tests.yml`、`rag/compose.yaml`、`git log --oneline -30`、`rag/.env.example`（存在性确认）。

RAG 源码：`config/config.py`、`app/routers/{ask,documents,prompt_eval}.py`、`services/{ask_service,prompt_eval_service}.py`、`retrieval/{query_analyzer,retriever}.py`、`infrastructure/{vector_store,llm_client}.py`、`ingestion/chunkers/{markdown_chunker,pdf_chunker}.py`、`evaluation/{answer_judge,judge_schema}.py`。

Agent 源码：`service.py`、`orchestration/{planner,executor}.py`、`tools/{registry,retrieval,summary,question_decompose}.py`。

结构与测试：`rag/src`、`rag/tests`、`agent/src`、`agent/tests` 目录树；测试函数计数 rag 119 / agent 47（2026-06-09 实跑全绿）；logging 已接入 RAG 服务边界。

仅引用既有报告、未直接打开（需实跑复核）：`experiments/latency_benchmark.md`（~24.43s，瓶颈在生成）、`experiments/large_pdf_ingestion_report.md`（gpt4 report，100 页，452 chunks）、`experiments/judge_runs/*.json`（3 个）、`retrieval_eval_cases.json`（11 cases）。
