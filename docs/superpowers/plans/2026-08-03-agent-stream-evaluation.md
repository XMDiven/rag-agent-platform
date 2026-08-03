# Agent Stream Stability Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repeatable evaluation runner that measures Agent stream protocol correctness, completion reliability, mixed-output rate, and latency without storing answer or source content.

**Architecture:** Reuse the existing `AgentEvalCase` dataset loader and consume `stream_agent_events()` directly. A pure per-case evaluator records event metadata and timings; report aggregation and the CLI follow the existing Agent evaluation conventions. Unit tests inject event iterators and deterministic timers; the real run remains outside CI.

**Tech Stack:** Python 3.11, Pydantic stream events, pytest, uv.

---

### Task 1: Per-case stream evaluation

**Files:**
- Create: `agent/src/agent_app/scripts/evaluate_agent_stream.py`
- Create: `agent/tests/scripts/test_evaluate_agent_stream.py`

- [ ] **Step 1: Write failing tests for a successful stream and a mixed-output failure**

Tests construct `StepEvent`, `AnswerDeltaEvent`, `SourcesEvent`, `ErrorEvent`, and `DoneEvent`, inject a deterministic timer, and assert that the evaluator returns only metadata:

```python
result = evaluate_stream_case(case, stream_fn=fake_stream, timer=fake_timer)
assert result["event_types"] == ["step", "answer_delta", "sources", "done"]
assert result["first_event_latency_seconds"] == 1.0
assert result["first_answer_latency_seconds"] == 2.0
assert result["answer_character_count"] == 3
assert "answer" not in result
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest agent/tests/scripts/test_evaluate_agent_stream.py -q`

Expected: import failure because `evaluate_agent_stream.py` does not exist.

- [ ] **Step 3: Implement minimal event collection and protocol checks**

The evaluator must track `step_count`, `source_count`, answer character count, first-event/first-answer/total latency, final metadata, error code, and event types. It must reject missing/multiple/non-final `done`, events after `done`, invalid order, and unhandled exceptions without recording exception messages.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest agent/tests/scripts/test_evaluate_agent_stream.py -q`

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent/src/agent_app/scripts/evaluate_agent_stream.py agent/tests/scripts/test_evaluate_agent_stream.py
git commit -m "feat(agent): evaluate stream stability per case"
```

### Task 2: Summary, report persistence, and CLI

**Files:**
- Modify: `agent/src/agent_app/scripts/evaluate_agent_stream.py`
- Modify: `agent/tests/scripts/test_evaluate_agent_stream.py`

- [ ] **Step 1: Write failing tests for summary metrics and CLI exit behavior**

The expected summary contains completion, normal-termination, mixed-output, and protocol-valid rates plus average/P95 first-event, first-answer, and total latency. Missing first-answer values are excluded from that latency denominator.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest agent/tests/scripts/test_evaluate_agent_stream.py -q`

Expected: missing `summarize_results`, `run_evaluation`, `write_report`, or CLI behavior.

- [ ] **Step 3: Implement aggregation and CLI**

The CLI reuses `load_cases()`, prints progress per case, writes timestamped JSON under `agent/experiments/runs/streaming/`, and exits nonzero only when a protocol violation or unhandled runner error occurs. Model-level `error → done` remains a measured stability outcome rather than a broken evaluator.

- [ ] **Step 4: Run focused and full automated tests**

Run:

```bash
uv run pytest agent/tests/scripts/test_evaluate_agent_stream.py -q
uv run pytest -q
```

Expected: all tests pass with only the two known dependency deprecation warnings in the full suite.

- [ ] **Step 5: Commit**

```bash
git add agent/src/agent_app/scripts/evaluate_agent_stream.py agent/tests/scripts/test_evaluate_agent_stream.py
git commit -m "feat(agent): report stream stability metrics"
```

### Task 3: Documentation and real baseline

**Files:**
- Modify: `agent/experiments/README.md`
- Create after review: `agent/experiments/reports/streaming/baseline_2026-08-03.md`

- [ ] **Step 1: Document the command and metric semantics**

Document:

```bash
uv run python -m agent_app.scripts.evaluate_agent_stream
```

Clarify that reports omit answer text/source snippets and that mixed output is counted, not treated as evaluator corruption.

- [ ] **Step 2: Run the real 12-case baseline**

Start existing local dependencies, load the ignored `.env`, run the CLI, and inspect the generated JSON for secrets or content before creating a concise committed Markdown baseline.

- [ ] **Step 3: Run final verification**

Run:

```bash
uv run pytest -q
git diff --check
```

Expected: all tests pass and no whitespace errors.

- [ ] **Step 4: Commit**

```bash
git add agent/experiments/README.md agent/experiments/reports/streaming/baseline_2026-08-03.md
git commit -m "docs(agent): record stream stability baseline"
```
