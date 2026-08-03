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
    └── judge/                  # 答案质量 Judge JSON，默认被 Git 忽略
```

## 前置条件

真实评测会调用现有 `run_agent()`，因此需要：

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

指定其他数据集或输出目录：

```bash
uv run python -m agent_app.scripts.evaluate_agent \
  --cases agent/experiments/datasets/agent_eval_cases.json \
  --output-dir agent/experiments/runs/evaluation
```

全部 case 通过时进程退出码为 `0`；任一 case 失败时为 `1`。单个 case 的模型调用异常会被记录为 `agent_error`，不会阻止剩余 case 运行。

## Case 契约

每个 case 定义允许和必须出现的外层 Agent 工具、允许的结束原因、是否必须返回来源，以及轨迹最大步数。`question_decompose_tool` 内部执行的检索不会作为独立外层 step 重复计数。评测不对答案做字符串精确匹配，避免模型措辞差异产生误报。

## 报告指标

- `pass_rate`：满足全部 case 约束的比例；
- `normal_termination_rate`：以 `final_answer` 或规则式 `single_step` 正常结束的比例；
- `tool_constraint_pass_rate`：工具集合满足允许和必须约束的比例；
- `source_constraint_pass_rate`：需要来源的 case 实际返回来源的比例；
- `average_latency_seconds`、`p95_latency_seconds`：完整 Agent run 的平均与 P95 延迟。

运行结果默认不提交。需要保留代表性基线时，人工复核模型、语料、配置和报告内容后再使用 `git add -f` 添加。

## 两层评测的区别

- `evaluate_agent` 检查工具轨迹、结束原因、来源约束和延迟，不评价答案语义。
- `evaluate_agent_answers` 重新运行相同 case，并复用 RAG Judge 评价相关性、完整性、证据支撑和格式。

答案 Judge 第一版与 Agent 使用同一个配置模型，报告会标记 `judge_independence: same_model`。它适合作为内部回归信号，不等同于独立模型或人工评审。Judge 运行有额外模型成本和波动，因此不进入默认 CI。全部 case 通过时退出码为 `0`，任一 Agent/Judge case 失败时为 `1`。

已人工复核的首次真实结果见：

- [`reports/evaluation/baseline_2026-08-03.md`](reports/evaluation/baseline_2026-08-03.md)：编排契约 12/12；
- [`reports/judge/baseline_2026-08-03.md`](reports/judge/baseline_2026-08-03.md)：答案 Judge 5/12，并区分 rubric 不适用与真实 groundedness 问题。
