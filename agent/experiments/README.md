# Agent 实验与评测

本目录保存 Agent 评测输入与运行产物，用于验证真实模型的工具选择、结束状态、来源返回和延迟是否发生回归。

## 目录

```text
experiments/
├── datasets/
│   └── agent_eval_cases.json   # 人工维护的 golden set
├── reports/
│   └── evaluation/             # 人工复核并提交的 baseline 报告
└── runs/
    ├── evaluation/             # 编排评测 JSON，默认被 Git 忽略
    ├── judge/                  # 答案质量 Judge JSON，默认被 Git 忽略
    └── streaming/              # 流协议与稳定性评测 JSON，默认被 Git 忽略
```

## 前置条件

编排和答案质量评测直接调用现有 `run_agent()`，流式评测通过 HTTP 调用运行中的 Agent 服务。真实评测需要：

- 已根据根目录说明完成 `uv sync`；
- Qdrant 正在运行且已经摄入评测问题对应的知识库文档；
- `.env` 中存在可用的 LLM API 配置。

## 运行

在仓库根目录执行：

```bash
uv run python -m agent_app.scripts.evaluate_agent
```

运行 Agent 最终答案 LLM-as-Judge：

```bash
uv run python -m agent_app.scripts.evaluate_agent_answers
```

运行 Agent 流式稳定性评测前，先在另一个终端启动 Agent API（Docker Compose 已启动该服务时可跳过）：

```bash
uv run uvicorn agent_app.app.main:app --host 127.0.0.1 --port 8002
```

然后运行评测：

```bash
uv run python -m agent_app.scripts.evaluate_agent_stream
```

默认请求 `http://127.0.0.1:8002/agent/run/stream`。可用 `--url` 指定其他接口地址、`--timeout 120` 设置 HTTP 连接、读、写和连接池等待超时（秒）；读超时限制两次接收数据之间的等待，不是整个 Agent 任务的总时间上限。脚本逐行读取 NDJSON，不等待完整响应才解析事件。真实运行仍会产生模型调用费用。

指定其他数据集或输出目录：

```bash
uv run python -m agent_app.scripts.evaluate_agent \
  --cases agent/experiments/datasets/agent_eval_cases.json \
  --output-dir agent/experiments/runs/evaluation
```

`evaluate_agent` 全部 case 通过时进程退出码为 `0`；任一 case 失败时为 `1`。单个 case 的模型调用异常会被记录为 `agent_error`，不会阻止剩余 case 运行。流式评测的退出码规则见下文。

## A2 三种方案的统一入口

`compare_strategies` 复用原有 RAG 检索题，默认选择 `langchain_llamaindex_comparison`，三条路径接收完全相同的问题：

| 策略 | 执行方式 |
| --- | --- |
| `rag` | 原问题直接调用现有 RAG 问答服务 |
| `fixed` | 规则拆解 → 各子问题调用 RAG → 基于聚合原文证据汇总一次；任一子问题失败或缺少资料时记为失败 |
| `agent` | 调用现有 `run_agent()`，由模型决定工具和执行轮次 |

默认只预览现有题目、固定流程的子问题和共享配置，不创建模型或访问向量库：

```bash
uv run --no-sync python -m agent_app.scripts.compare_strategies
```

真实执行需要事先确认预算，再显式加 `--run`：

```bash
uv run --no-sync python -m agent_app.scripts.compare_strategies \
  --only langchain_llamaindex_comparison --run
```

`--only` 可传逗号分隔的已有 RAG case id；`--cases` 可指定同结构的已有检索集。默认结果写入 `agent/experiments/runs/comparison/`，被 Git 忽略。该目录中的 JSON 保存每种策略同次生成的答案、来源原文、Judge 证据与分数、结束原因、调用和 token 用量、耗时及错误类型；原题集保持不变。真实运行涉及答案生成及三份 Judge 评分，不能把一次 CLI 执行理解为一次模型请求。

三条路径共享模型、检索及评分配置，运行期间关闭本地答案缓存并在结束后恢复；供应商仍可能使用自己的 token 缓存。对三种方案使用相同的 RAG Judge，不要求直接 RAG 或固定流程满足 Agent 专属的工具选择契约，也不会为评分重新生成一次答案。脚本分别记录生成阶段与 Judge 阶段的用量和耗时，便于比较实际业务消耗与评分开销。模型用量来自现有响应回调，失败且没有 usage 的请求不会被计入，不能把 `llm_calls` 当作请求预算控制器。

`retrieval_calls` 指调用问答服务的次数，含子问题，不是底层 Qdrant 请求次数；`tool_calls` 只统计 Agent 的外层工具调用，固定流程的普通函数调用记为 0。缺答案、缺来源、异常、非正常结束及 Judge 未通过均计为失败；一个策略失败后继续记录其余策略，CLI 在存在失败时返回 1。

当前入口用于 A2.1 路径验证：共享配置不代表索引已经冻结，也没有实现三种方案的共同调用/上下文预算上限。本阶段仅比较完整同步回答，不测 HTTP 首事件或首答案延迟；批量统计、统一预算及收益结论仍需在 A2.2 继续完成。运行结果含答案和来源正文，分享前检查内容。

## 编排评测的 Case 契约

每个 case 定义允许和必须出现的外层 Agent 工具、允许的结束原因、是否必须返回来源，以及轨迹最大步数。`question_decompose_tool` 内部执行的检索不会作为独立外层 step 重复计数。编排评测不对答案做字符串精确匹配，避免模型措辞差异产生误报。流式评测复用这些问题，但只检查流协议、完成状态和延迟，不执行这些工具或来源约束，也不评价答案语义。

`langchain_vs_llamaindex` 的人工证据与回答标准见 [现有比较题标注](../../rag/experiments/datasets/comparison_case_annotations.md)，与 RAG 集中的对应题共享内容标准。当前 runner 不自动读取该文档，工具约束保持不变。

## 报告指标

- `pass_rate`：满足全部 case 约束的比例；
- `normal_termination_rate`：以 `final_answer` 或规则式 `single_step` 正常结束的比例；
- `tool_constraint_pass_rate`：工具集合满足允许和必须约束的比例；
- `source_constraint_pass_rate`：需要来源的 case 实际返回来源的比例；
- `average_latency_seconds`、`p95_latency_seconds`：完整 Agent run 的平均与 P95 延迟。

运行结果默认不提交。需要保留代表性基线时，人工复核模型、语料、配置和报告内容后再使用 `git add -f` 添加。

## 三种评测的区别

| 入口 | 执行路径 | 检查内容 |
| --- | --- | --- |
| `evaluate_agent` | 进程内 `run_agent()` 同步 loop，未处理异常时可单步降级 | 工具轨迹、结束原因、来源约束和延迟 |
| `evaluate_agent_answers` | 重新运行同步 `run_agent()`，再调用 Judge | 最终答案的相关性、完整性、证据支撑和格式 |
| `evaluate_agent_stream` | HTTP `/agent/run/stream` 异步 loop，异常以 `error/done` 结束 | 事件格式与顺序、完成状态、首事件/首答案/完整流耗时 |

微调 router 只影响配置为 `finetuned` 时的非空单步规划，不参与正常 loop，也不参与当前 HTTP 流的异常处理；具体选择顺序见 [`agent/README.md`](../README.md#工具规划策略)。

答案 Judge 第一版与 Agent 使用同一个配置模型，报告会标记 `judge_independence: same_model`。它适合作为内部回归信号，不等同于独立模型或人工评审。Judge 运行有额外模型成本和波动，因此不进入默认 CI。全部 case 通过时退出码为 `0`，任一 Agent/Judge case 失败时为 `1`。

答案评测的每个 case 现在保存 `judge_evidence`：与 Judge 输入一致的证据文本，包含引用编号、来源、章节和实际片段；摘要题保存用户提供的原文。`sources` 仍保留原有去除 snippet 的元数据格式。未调用 Judge 的 case 记为 `null`；Judge 调用失败时保留已准备的证据，错误仍只记录类型。旧报告缺少此字段，不能据此还原当时的片段。证据正文只随本地运行 JSON 保存，运行目录继续被 Git 忽略；分享报告前应检查其中的文档或用户原文。

流式评测通过 `POST /agent/run/stream` 访问当前异步执行路径，将收到的 NDJSON 校验为版本化事件，再复用原有事件顺序和完成状态检查。报告标记 `transport: http`，记录事件类型、工具名、计数、结束状态，以及客户端收到首事件、首答案和完整流的耗时；不保存答案正文、来源对象、snippet、工具参数或异常消息。模型以稳定的 `error → done` 结束时会计入流失败率，例如 `mixed_model_output`，但不会被当作评测器损坏；缺少 `done`、连接中断、事件顺序错误、格式错误或 HTTP 请求异常均属于协议无效，并使 CLI 返回非零。脚本直接访问 Agent API，不包含 BFF 或浏览器的耗时。

已人工复核的首次真实结果见：

- [`reports/evaluation/baseline_2026-08-03.md`](reports/evaluation/baseline_2026-08-03.md)：编排契约 12/12；
- [`reports/judge/baseline_2026-08-03.md`](reports/judge/baseline_2026-08-03.md)：答案 Judge 5/12，并区分 rubric 不适用与真实 groundedness 问题；
- [`reports/streaming/baseline_2026-08-03.md`](reports/streaming/baseline_2026-08-03.md)：流完成、协议有效和成功结束均为 12/12，并记录首事件、首答案和总耗时。

2026-08-03 的流式基线使用旧的进程内同步执行路径，保留原测试范围，不能当作当前 HTTP 异步接口的新基线。当前改造已通过假模型和模拟 HTTP 响应验证，并用本地真实 HTTP 服务检查首事件能在回答结束前到达；真实模型的新基线需要另行运行。

2026-10-24 复用了已有 LangChain / LlamaIndex 比较题做[单次运行与人工复核](reports/judge/comparison_manual_review_2026-10-24.md)：同次工具流程及 Judge 评分通过，但人工发现引用错配和缺证据的协作结论；报告保存自动与人工结论的差异，不作为整套数据集的新通过率。
