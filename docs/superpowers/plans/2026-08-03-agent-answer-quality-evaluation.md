# Agent Answer Quality Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the first real Agent orchestration baseline and add a separate LLM-as-Judge runner for final-answer quality.

**Architecture:** Reuse the existing Agent golden set, `run_agent()`, shared RAG `judge_answer()`, and configured `kimi-k2.6` client. Keep Agent answers and full evidence in memory for judging, persist only sanitized source metadata plus scores, and isolate Agent/Judge failures per case.

**Tech Stack:** Python 3.11+, argparse, dataclasses, pathlib, JSON, existing LangChain Judge chain, pytest, uv

---

### Task 1: Define safe Agent answer extraction and case evaluation

**Files:**
- Create: `agent/tests/scripts/test_evaluate_agent_answers.py`
- Create: `agent/src/agent_app/scripts/evaluate_agent_answers.py`

- [ ] **Step 1: Write failing tests for successful evaluation**

Create fake `AgentRunResult`-shaped objects and a real `AnswerJudgeResult`. Assert `evaluate_case()`:

- calls the injected Agent once;
- passes the question, extracted answer, and full sources including snippets to the injected Judge once;
- records `termination_reason`, ordered outer tool names, step count, score object, pass status, and three durations;
- persists sources without the `snippet` field.

Use this API:

```python
result = evaluate_case(
    case,
    judge_llm=object(),
    run_agent_fn=fake_run_agent,
    judge_fn=fake_judge,
    timer=fake_timer,
)
```

- [ ] **Step 2: Run the focused test and verify RED**

```bash
uv run pytest agent/tests/scripts/test_evaluate_agent_answers.py -q
```

Expected: import error because `evaluate_agent_answers` does not exist.

- [ ] **Step 3: Implement minimal extraction and success path**

Implement focused helpers:

```python
def extract_answer(result: Any) -> str: ...
def extract_sources(result: Any) -> list[dict[str, Any]]: ...
def sanitize_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
def evaluate_case(...dependencies...) -> dict[str, Any]: ...
```

Reuse `extract_tool_names()` from the existing orchestration evaluator. Remove only `snippet` from persisted source dictionaries; retain it in the in-memory list passed to Judge.

- [ ] **Step 4: Verify GREEN**

Run the focused test. Expected: success-path tests pass.

### Task 2: Isolate failures without leaking exception messages

**Files:**
- Modify: `agent/tests/scripts/test_evaluate_agent_answers.py`
- Modify: `agent/src/agent_app/scripts/evaluate_agent_answers.py`

- [ ] **Step 1: Write failing failure-path tests**

Cover separately:

- Agent raises `RuntimeError("api_key=secret")`: Judge is not called, result has `failure_stage="agent"`, only error type is persisted, and serialized result excludes the secret.
- Agent returns an empty answer: Judge is not called and `failure_stage="empty_answer"`.
- Judge raises `ValueError("token=secret")`: result retains sanitized Agent output, has `failure_stage="judge"`, persists only error type, and excludes the secret.

- [ ] **Step 2: Run tests and verify RED**

Expected: new assertions fail because failure isolation is absent.

- [ ] **Step 3: Implement failure paths**

Catch only `Exception`. Return `judge=None`, `passed=False`, a stable failure reason, safe error type, and measured durations. Do not catch process-level interrupts.

- [ ] **Step 4: Verify GREEN**

Run focused tests and expect all case-evaluation tests to pass.

### Task 3: Add aggregate metrics, progress, reports, and CLI

**Files:**
- Modify: `agent/tests/scripts/test_evaluate_agent_answers.py`
- Modify: `agent/src/agent_app/scripts/evaluate_agent_answers.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing summary tests**

Assert `summarize_results()` returns:

```python
{
    "total": 3,
    "passed": 2,
    "failed": 1,
    "pass_rate": 0.667,
    "average_scores": {
        "relevance": 4.5,
        "completeness": 4.0,
        "groundedness": 4.5,
        "format": 4.0,
    },
    "average_agent_duration_seconds": ...,
    "p95_agent_duration_seconds": ...,
    "average_judge_duration_seconds": ...,
    "p95_judge_duration_seconds": ...,
    "average_total_duration_seconds": ...,
    "p95_total_duration_seconds": ...,
    "agent_failed_count": 0,
    "judge_failed_count": 1,
}
```

Average scores use only cases with a valid Judge result; empty inputs return zero metrics. Reuse `nearest_rank_percentile()` from `evaluate_agent`.

- [ ] **Step 2: Implement and verify summary metrics**

Run focused tests and expect summary tests to pass.

- [ ] **Step 3: Write failing orchestration, progress, report, and CLI tests**

Test that `run_evaluation()` runs all cases in order, prints `Evaluating Agent Judge case N/total`, continues after a failed case, and adds:

```python
{
    "run_id": "...",
    "judge_model_id": "kimi-k2.6",
    "judge_independence": "same_model",
    "summary": {...},
    "cases": [...],
}
```

Test `write_report()` creates directories and round-trips JSON. Test `main()` returns 0 only when `summary.failed == 0`, otherwise 1.

- [ ] **Step 4: Implement orchestration and CLI**

Support `--cases` and `--output-dir`, defaulting to the existing Agent dataset and `agent/experiments/runs/judge/`. Use `get_client()` once for the Judge. Print report location and pass summary. End with `raise SystemExit(main())`.

- [ ] **Step 5: Ignore generated Judge JSON**

Add only:

```gitignore
agent/experiments/runs/judge/*.json
```

- [ ] **Step 6: Verify GREEN**

Run the focused suite and expect all tests to pass.

### Task 4: Preserve baseline evidence and document usage

**Files:**
- Create: `agent/experiments/reports/evaluation/baseline_2026-08-03.md`
- Modify: `agent/experiments/README.md`
- Modify: `agent/README.md`
- Modify: `README.md`

- [ ] **Step 1: Write the human baseline report**

Record the actual run configuration and results:

- `kimi-k2.6`, `documents` collection, 22,387 points, `bge-m3:latest`;
- 12/12, all four orchestration rates 100%;
- average 24.677 seconds and P95 71.637 seconds;
- per-case latency/trajectory summary;
- explicit boundary that orchestration success does not prove semantic answer quality;
- raw JSON path is ignored and the Markdown report is the committed evidence.

- [ ] **Step 2: Document the Judge command and limitations**

Add:

```bash
uv run python -m agent_app.scripts.evaluate_agent_answers
```

Document same-model self-evaluation, output location, exit codes, no default CI, and the distinction between orchestration and answer-quality evaluation.

- [ ] **Step 3: Run documentation and focused checks**

Run `git diff --check`, scan for placeholder text, and rerun the focused test file.

### Task 5: Full verification, real evaluations, report, and commit

**Files:**
- Verify all files above.

- [ ] **Step 1: Run all deterministic verification**

```bash
uv run pytest -q
uv run python -m agent_app.scripts.evaluate_agent_answers --help
cd frontend
npm run lint
npm run build
```

Expected: all commands exit 0.

- [ ] **Step 2: Run the real orchestration evaluation**

With Qdrant, Ollama, and model credentials available:

```bash
uv run python -m agent_app.scripts.evaluate_agent
```

Expected acceptance: 12/12 pass.

- [ ] **Step 3: Run the real answer-quality evaluation**

```bash
uv run python -m agent_app.scripts.evaluate_agent_answers
```

Expected acceptance: all 12 cases complete and at least 10 pass. If fewer than 10 pass, preserve the evidence and report the actual failed cases; do not weaken the Judge threshold or golden set to force success.

- [ ] **Step 4: Inspect generated report safety**

Verify the JSON contains no `snippet`, `api_key`, raw exception message, or environment secret. Do not stage the ignored machine report.

- [ ] **Step 5: Commit implementation**

```bash
git add .gitignore README.md agent/README.md agent/experiments/README.md agent/experiments/reports/evaluation/baseline_2026-08-03.md agent/src/agent_app/scripts/evaluate_agent_answers.py agent/tests/scripts/test_evaluate_agent_answers.py docs/superpowers/plans/2026-08-03-agent-answer-quality-evaluation.md
git commit -m "feat(agent): add answer quality evaluation"
```
