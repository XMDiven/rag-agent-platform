# Agent 实验与评测

本目录保存 Agent 评测输入与运行产物，用于验证真实模型的工具选择、结束状态、来源返回和延迟是否发生回归。

## 目录

```text
experiments/
├── datasets/
│   └── agent_eval_cases.json   # 人工维护的 golden set
└── runs/
    └── evaluation/             # 自动生成的 JSON 报告，默认被 Git 忽略
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

指定其他数据集或输出目录：

```bash
uv run python -m agent_app.scripts.evaluate_agent \
  --cases agent/experiments/datasets/agent_eval_cases.json \
  --output-dir agent/experiments/runs/evaluation
```

全部 case 通过时进程退出码为 `0`；任一 case 失败时为 `1`。单个 case 的模型调用异常会被记录为 `agent_error`，不会阻止剩余 case 运行。

## Case 契约

每个 case 定义允许和必须出现的工具、允许的结束原因、是否必须返回来源，以及轨迹最大步数。评测不对答案做字符串精确匹配，避免模型措辞差异产生误报。

## 报告指标

- `pass_rate`：满足全部 case 约束的比例；
- `normal_termination_rate`：以 `final_answer` 或规则式 `single_step` 正常结束的比例；
- `tool_constraint_pass_rate`：工具集合满足允许和必须约束的比例；
- `source_constraint_pass_rate`：需要来源的 case 实际返回来源的比例；
- `average_latency_seconds`、`p95_latency_seconds`：完整 Agent run 的平均与 P95 延迟。

运行结果默认不提交。需要保留代表性基线时，人工复核模型、语料、配置和报告内容后再使用 `git add -f` 添加。
