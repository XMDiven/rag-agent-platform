# Agent 答案质量评测设计

## 目标

在现有 Agent 编排评测之外，增加一套独立的答案质量评测，验证最终答案是否相关、完整、有检索证据支撑且格式清晰。

本次同时把 2026-08-03 的第一次真实编排评测保存为人工 baseline 报告。延迟优化、独立 Judge 模型、引用逐条核验和新工具不在本次范围。

## 当前基线与缺口

现有 12-case 编排评测已在真实环境运行：

- 12/12 case 通过；
- 编排、正常结束、工具约束和来源约束通过率均为 100%；
- 平均延迟 24.677 秒；
- P95 延迟 71.637 秒。

该评测只检查工具轨迹、结束原因、来源数量和步数。最终答案即使不相关、不完整或没有被来源支持，也可能通过，因此不能把 12/12 表述为答案质量 100%。

## 方案选择

考虑过三种方案：

1. 复用现有 RAG LLM-as-Judge。改动最小，评分结构和 Prompt 已有测试，本次采用。
2. 为 Agent 单独配置 Judge 模型。独立性更强，但需要新配置、客户端和运行成本，留到后续。
3. 为每个 case 维护关键词或 expected facts。确定性更强，但难覆盖开放式答案，维护成本高，不作为第一版。

第一版使用与 Agent 相同的 `kimi-k2.6` 作为 Judge。报告和文档必须明确这是同模型自评，结果适合作为内部回归信号，不等同于独立人工评价。

## 架构

新增独立脚本 `agent_app.scripts.evaluate_agent_answers`。它复用：

- `agent_app.service.run_agent()` 生成 Agent 答案；
- `rag_app.infrastructure.llm_client.get_client()` 创建 Judge LLM；
- `rag_app.evaluation.answer_judge.judge_answer()` 执行评分；
- `agent/experiments/datasets/agent_eval_cases.json` 提供相同的 12 个问题。

不把 Judge 合并进现有 `evaluate_agent`：编排评测应保持快速、确定性逻辑清晰；答案评测有额外模型调用、成本和波动，必须独立运行。

## 数据流

```text
agent_eval_cases.json
→ run_agent(question)
→ answer + sources + termination_reason + steps
→ judge_answer(question, answer, sources, judge_llm)
→ scores + overall_pass + feedback
→ per-case result + aggregate summary
→ timestamped JSON report
```

每个 case 只运行一次 Agent；同一次结果同时提供给 Judge，不为不同评分维度重复运行 Agent。

## 评分维度

直接复用现有 `AnswerJudgeResult`：

- `relevance_score`：是否直接回答问题；
- `completeness_score`：是否覆盖关键点；
- `groundedness_score`：是否由提供的来源证据支持；
- `format_score`：结构是否清晰；
- `overall_pass`：四项分数均不低于 4 时为真；
- `feedback`：Judge 对不足之处的简短说明。

第一版不新增单独的 `citation_correctness_score`。引用与结论是否匹配暂时由 groundedness 覆盖，避免修改共享 Judge schema 并影响现有 RAG 评测。

## 结果格式

每个 case 保存：

- case id 和问题；
- Agent 最终答案；
- 去除 `snippet` 后的来源元数据；
- Agent 结束原因、工具序列和步数；
- Judge 四项分数、`overall_pass` 和反馈；
- Agent、Judge 和总耗时；
- 失败阶段和安全错误类型。

完整来源片段只在内存中传给 Judge，不写入 Agent Judge 报告，避免复制大段文档正文。报告不得保存异常消息，因为 SDK 异常可能包含密钥、认证 URL 或请求内容。

汇总保存：

- total、passed、failed 和 pass rate；
- 四个评分维度的平均分；
- Agent、Judge 和总耗时的平均值与 P95；
- `agent_failed_count` 和 `judge_failed_count`；
- Judge 模型 id；
- `judge_independence: same_model`。

## 错误处理

- 数据集无效：复用现有 `load_cases()`，立即失败且不生成报告。
- 单个 Agent 调用异常：记录 `agent_failed` 和异常类型，跳过该 case 的 Judge，继续剩余 case。
- Agent 返回空答案：不调用 Judge，记录 `empty_answer`。
- Judge 调用或结构化解析失败：记录 `judge_failed` 和异常类型，继续剩余 case。
- `KeyboardInterrupt` 等进程级中断不捕获。
- 任意失败 case 都使 CLI 返回退出码 1；全部通过返回 0。

运行时打印逐 case 开始与完成信息，避免长时间无输出。

## 文件与产物

- 新增 `agent/src/agent_app/scripts/evaluate_agent_answers.py`；
- 新增 `agent/tests/scripts/test_evaluate_agent_answers.py`；
- 新增 `agent/experiments/reports/evaluation/baseline_2026-08-03.md`；
- 更新 `agent/experiments/README.md` 和必要的根文档；
- JSON 运行结果写入 `agent/experiments/runs/judge/` 并默认被 Git 忽略。

只提交人工 baseline 报告和有代表性的结论，不默认提交机器生成的每次 Judge 运行结果。

## 测试策略

严格按 TDD 实现，单元测试注入假的 Agent 和 Judge，不访问 Qdrant 或真实 LLM。覆盖：

- Agent 成功且 Judge 通过；
- Agent 抛异常后继续；
- 空答案不调用 Judge；
- Judge 抛异常或解析失败后继续；
- 来源持久化时移除 snippet；
- 异常消息不进入报告；
- 四项平均分和延迟 P95；
- 逐 case 进度输出；
- JSON 报告写入；
- CLI 退出码。

完成后运行全部 Python 测试、前端 lint 和 production build。真实 Judge 评测不进入默认 CI。

## 成功标准

- 新脚本可通过 `uv run python -m agent_app.scripts.evaluate_agent_answers` 运行；
- 12 个 case 全部完成，不因单 case 失败中断；
- Judge 至少 10/12 通过；
- 编排评测重新运行仍为 12/12；
- 报告明确同模型自评限制；
- 真实报告不包含密钥、异常消息或完整文档 snippet；
- 不新增依赖，不改变 Agent API 和现有 RAG Judge schema。

## 后续但不在本次范围

- 独立 Judge 模型或人工抽检；
- 单独的引用正确性评分；
- 多次运行的方差与置信区间；
- Judge 结果接入定时任务或 CI；
- Agent 延迟优化。
