# Agent Evaluation Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dependency-free, deterministic evaluation runner and a 12-case golden set for the existing multi-step Agent.

**Architecture:** Keep evaluation logic in one focused script with pure helpers for case validation, trajectory checks, percentile calculation, and summary generation. The outer runner injects the existing `run_agent` callable, records timing, continues after per-case exceptions, and writes timestamped JSON artifacts outside version control.

**Tech Stack:** Python 3.11+, dataclasses, pathlib, argparse, json, pytest, existing `agent_app.service.run_agent`

---

### Task 1: Define and validate evaluation cases

**Files:**
- Create: `agent/tests/scripts/test_evaluate_agent.py`
- Create: `agent/src/agent_app/scripts/evaluate_agent.py`

- [ ] **Step 1: Write failing tests for valid and invalid datasets**

Add tests that write JSON to `tmp_path`, call `load_cases()`, and assert a typed `AgentEvalCase` is returned. Add parameterized invalid cases for a non-list root, duplicate ids, unknown keys, invalid list element types, negative `max_steps`, and empty `allowed_termination_reasons`; each must raise `ValueError` containing the case id or dataset location.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run pytest agent/tests/scripts/test_evaluate_agent.py -q
```

Expected: collection fails because `agent_app.scripts.evaluate_agent` does not exist.

- [ ] **Step 3: Implement the case contract and loader**

Create:

```python
@dataclass(frozen=True)
class AgentEvalCase:
    id: str
    question: str
    allowed_tools: list[str]
    required_tools: list[str]
    allowed_termination_reasons: list[str]
    requires_sources: bool
    max_steps: int
```

Implement `parse_case(raw, index)`, `validate_string_list(value, field, label)`, and `load_cases(path=DEFAULT_CASES_PATH)`. Reject booleans where integers are expected, reject duplicate ids, reject required tools not included in allowed tools, and reject unknown fields so dataset typos fail loudly.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the focused command from Step 2. Expected: all case-loading tests pass.

### Task 2: Evaluate trajectories and summarize metrics

**Files:**
- Modify: `agent/tests/scripts/test_evaluate_agent.py`
- Modify: `agent/src/agent_app/scripts/evaluate_agent.py`

- [ ] **Step 1: Write failing tests for tool extraction and case checks**

Use small fake result objects matching `AgentRunResult`. Cover:

- loop steps extract tools in call order and ignore final-answer steps;
- a `single_step` result uses `plan.tool.name` as its one selected tool;
- disallowed tool and missing required tool produce separate failure reasons;
- unexpected termination, missing sources, and excess steps each fail independently;
- a fully matching result passes.

- [ ] **Step 2: Run focused tests and verify RED**

Expected: failures for missing `extract_tool_names()` and `evaluate_result()`.

- [ ] **Step 3: Implement minimal deterministic checks**

Implement:

```python
def extract_tool_names(result: AgentRunResult) -> list[str]: ...
def evaluate_result(case: AgentEvalCase, result: AgentRunResult) -> dict[str, Any]: ...
```

Return a serializable dictionary containing expected constraints, actual tools, termination reason, source count, step count, `passed`, and stable failure reason codes. Deduplicate no tools: repeated calls remain in order because trajectory shape is meaningful.

- [ ] **Step 4: Write failing tests for summary statistics**

Test empty input and a four-case sample. Assert total, passed, failed, pass rate, normal termination rate, tool-constraint pass rate, source-constraint pass rate, average latency, and nearest-rank P95 latency.

- [ ] **Step 5: Implement and verify summary statistics**

Implement `nearest_rank_percentile(values, percentile)` and `summarize_results(results)`. Round public float metrics to three decimals. Run the focused tests and expect all to pass.

### Task 3: Run cases, survive case errors, and write reports

**Files:**
- Modify: `agent/tests/scripts/test_evaluate_agent.py`
- Modify: `agent/src/agent_app/scripts/evaluate_agent.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing orchestration tests**

Inject a fake `run_agent_fn` and deterministic timer. Assert `run_evaluation()`:

- invokes every case in order;
- records latency;
- converts a normal `Exception` into a failed case with `agent_error` plus error type/message;
- continues to the next case;
- returns `{generated_at, summary, cases}`.

Add a report-writing test asserting parent directories are created and the JSON round-trips. Add a CLI test asserting exit code 0 when all cases pass and 1 when any case fails.

- [ ] **Step 2: Run focused tests and verify RED**

Expected: failures for missing orchestration/report functions.

- [ ] **Step 3: Implement orchestration and CLI**

Implement:

```python
def run_evaluation(cases, run_agent_fn=run_agent, timer=perf_counter) -> dict[str, Any]: ...
def write_report(report, output_dir=DEFAULT_OUTPUT_DIR, now=None) -> Path: ...
def build_parser() -> argparse.ArgumentParser: ...
def main(argv: list[str] | None = None) -> int: ...
```

Support `--cases` and `--output-dir`. Print the report path and a concise pass summary. End the module with `raise SystemExit(main())`. Catch `Exception` per case but allow process-level interrupts to propagate.

- [ ] **Step 4: Ignore generated Agent evaluation runs**

Add only this scoped pattern:

```gitignore
agent/experiments/runs/evaluation/*.json
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run `uv run pytest agent/tests/scripts/test_evaluate_agent.py -q`. Expected: all tests pass.

### Task 4: Add the golden set and documentation

**Files:**
- Create: `agent/experiments/datasets/agent_eval_cases.json`
- Create: `agent/experiments/README.md`
- Modify: `agent/README.md`
- Modify: `README.md`

- [ ] **Step 1: Add a dataset contract test before the dataset**

Add a test that calls `load_cases()` with the default path and asserts exactly 12 unique case ids with coverage for `fallback_tool`, `summary_tool`, `retrieval_tool`, and `question_decompose_tool`, plus at least one source-required case.

- [ ] **Step 2: Run the contract test and verify RED**

Expected: failure because the default dataset does not exist.

- [ ] **Step 3: Add 12 representative cases**

Use questions grounded in documents already tracked under `rag/data/raw/`. Keep expectations tolerant of valid alternate trajectories by using `allowed_tools`, while requiring the central tool for focused cases. Include empty input, summary, factual retrieval, comparison/decomposition, and multi-source questions.

- [ ] **Step 4: Document operation and correct stale boundaries**

Document:

```bash
uv run python -m agent_app.scripts.evaluate_agent
```

Explain that Qdrant, the indexed corpus, and an LLM API key are prerequisites; generated runs are ignored unless deliberately promoted as a baseline. Correct `agent/README.md` statements claiming that multi-round tool calling is not implemented. Update the root roadmap so Agent evaluation is listed as the current evidence-building milestone.

- [ ] **Step 5: Run dataset and focused tests**

Run `uv run pytest agent/tests/scripts/test_evaluate_agent.py -q`. Expected: all tests pass.

### Task 5: Full verification and commit

**Files:**
- Verify all modified and created files above.

- [ ] **Step 1: Inspect the complete diff**

Run:

```bash
git diff --check
git diff --stat
git diff
```

Confirm no unrelated files, secrets, generated runs, or placeholder text are included.

- [ ] **Step 2: Run all Python tests**

```bash
uv run pytest -q
```

Expected: all tests pass with no failures.

- [ ] **Step 3: Verify frontend lint and production build**

```bash
cd frontend
npm run lint
npx next build --webpack
```

Expected: lint exits 0 and Next.js compiles, type-checks, and generates all static pages.

- [ ] **Step 4: Verify the CLI without external calls**

Run `uv run python -m agent_app.scripts.evaluate_agent --help`. Expected: exit 0 and documented options. Do not run the live dataset unless Qdrant and model credentials are confirmed available.

- [ ] **Step 5: Commit the implementation**

```bash
git add .gitignore README.md agent/README.md agent/experiments agent/src/agent_app/scripts/evaluate_agent.py agent/tests/scripts/test_evaluate_agent.py docs/superpowers/plans/2026-08-03-agent-evaluation-baseline.md
git commit -m "feat(agent): add offline evaluation baseline"
```
