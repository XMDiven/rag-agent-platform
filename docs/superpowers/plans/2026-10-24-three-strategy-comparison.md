# 三种问答方案对照入口 Implementation Plan

> **For agentic workers:** 按当前用户已授权的 A2.1 范围逐项执行，先写失败测试，再做最小实现与审查。保持本地已有改动，不自动提交或推送。

**Goal:** 用现有题目和配置比较直接 RAG、固定拆解流程与 Agent，并保存同次答案、证据、用量与统一 Judge 结果。

**Architecture:** 新增一个脚本，复用 `ask_question`、`run_decomposed_retrieval`、`run_agent`、`generate_answer` 和现有 Judge/JSON 写入函数。固定流程在规则拆解和子问题 RAG 后，用聚合原文证据完成一次汇总；不直接拼接带局部引用的答案。三条路径共用配置、题目和评分规则。脚本默认只预览，`--run` 显式运行；不新增题集、依赖或业务 API。

**Tech Stack:** Python、现有 LangChain LCEL、pytest、标准库 argparse/unittest.mock。

## 文件与步骤

- Create: `agent/src/agent_app/scripts/compare_strategies.py`
- Test: `agent/tests/scripts/test_compare_strategies.py`
- Modify: `agent/experiments/README.md`，本地 `resume_alignment.md`。
- Modify: `.gitignore`，将新对照目录的 JSON 运行产物按现有规则忽略。

- [x] 写固定流程测试：复用拆解结果，最终汇总使用聚合原文及全局编号；子问题失败时不伪装成成功，不额外汇总。
- [x] 写对照入口测试：三条路径收到同一问题，答案只生成一次，Judge 对同次答案与证据评分；分别统计生成与 Judge 用量，隔离单方案错误。
- [x] 写缓存与预览测试：屏蔽已缓存的答案 getter，运行结束后恢复；预览不创建模型或访问向量库；无效 case 在模型创建前报错。
- [x] 运行 `uv run --no-sync pytest agent/tests/scripts/test_compare_strategies.py -q`，确认缺少模块/能力导致失败。
- [x] 实现最小函数 `run_fixed_workflow(question)`、`run_strategy(question, strategy)`、`run_comparison(cases, judge_llm, ...)` 和 `main(argv=None)`。

固定流程的数据路径：

```python
result = run_decomposed_retrieval(question)
# 先检查各子问题是否成功；无有效证据时不调用汇总模型。
answer = generate_answer(
    question=question,
    context=format_evidence(result["sources"]),
    llm=get_client(),
    prompt=get_qa_prompt(),
)
```

对照结果每行保存原 case id/问题、策略、答案/原文证据、结束原因、外层工具数、实际子问题问答调用数、生成 token/耗时、Judge token/耗时及异常类型。缺答案、缺来源、生成失败和 Judge 失败都计为失败；不对三种方案施加 Agent 专属工具契约。

- [x] 定向测试通过后运行 `uv run --no-sync pytest -q`，再检查 CLI 默认预览、`--help`、JSON 往返与文档链接。
- [x] 文档说明：同一配置并不等于冻结索引；本阶段只比较完整同步回答，不报告 HTTP 首事件/首答案延迟；共同调用/上下文预算与批量统计仍属于 A2.2/A3，不能宣称已完成公平收益实验。
- [x] 做只读审查，更新 roadmap，明确 A2.1 入口离线完成与真实三方案对照尚未运行的区别。

## 验证边界

测试用假模型和假检索，不访问外部服务。真实运行需要单独确认模型调用预算；本任务不复用此前单题预算，不自动运行整套数据或生产报告，也不把预览结果写成真实质量结论。
