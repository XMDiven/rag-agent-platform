# experiments —— 实验与评测目录

本目录按"输入 / 产物 / 报告"三层分离，避免把数据集、机器生成的运行结果和人工撰写的分析混在一层。新增内容时请遵守这个约定。

## 结构

```text
experiments/
├── datasets/     评测输入（人维护，代码读取）
├── runs/         评测运行产物（脚本自动生成，带时间戳）
│   ├── judge/        LLM-as-Judge 运行结果
│   └── evaluation/   检索 + 回答评估运行结果
└── reports/      人工撰写的实验报告（结论与方法论），按主题分类
    ├── retrieval/    检索质量（baseline、hybrid、chunk 等）
    ├── generation/   生成与回答质量（LLM-as-Judge、Prompt A/B、回答检查）
    ├── latency/      延迟 benchmark
    └── ingestion/    文档摄入验证
```

## 三层各自的职责

| 层 | 性质 | 谁写 | 谁读 | 能否随意改名/移动 |
|---|---|---|---|---|
| `datasets/` | 评测输入数据集（golden set 等） | 人手工维护 | 代码（脚本默认路径） | 否，改动需同步代码 |
| `runs/` | 自动生成的运行产物，文件名为时间戳 | 脚本 | `/prompt-evals` API、人工对比 | 否，目录被代码写死 |
| `reports/` | 人工撰写的结论 + 方法论 | 人 | 人 | 是，自由整理 |

## 代码耦合点（移动前必读）

以下路径在代码里写死，移动会破坏功能或 API：

- `datasets/retrieval_eval_cases.json` ← `scripts/evaluate_retrieval.py`（`DEFAULT_CASES_PATH`）
- `runs/judge/` ← `services/prompt_eval_service.py`（`JUDGE_RUNS_DIR`，`/prompt-evals` API 读取）、`scripts/evaluate_answers_with_judge.py`
- `runs/evaluation/` ← `scripts/run_eval.py`

`reports/` 下的所有 markdown 不被代码引用，可自由整理。

## 如何生成 runs/ 产物

```bash
# 检索 + 回答评估 → runs/evaluation/<timestamp>.json
conda run -n AI_DEV python -m rag_app.scripts.run_eval

# LLM-as-Judge → runs/judge/<timestamp>.json
conda run -n AI_DEV python -m rag_app.scripts.evaluate_answers_with_judge

# 延迟 benchmark（结果记入 reports/latency/latency_benchmark.md）
conda run -n AI_DEV python -m rag_app.scripts.benchmark_latency
```

## 约定

- 新报告放进对应主题子目录；带日期的实验用 `<主题>_<YYYY-MM-DD>.md`（如 `baseline_2026-06-05.md`）。
- `runs/` 是可重新生成的产物，仅保留有代表性/被报告引用的运行；不必把每次运行都长期留存。
- 报告里的数字以最近一次实跑为准，引用具体 run 文件时写明路径，便于复核。
