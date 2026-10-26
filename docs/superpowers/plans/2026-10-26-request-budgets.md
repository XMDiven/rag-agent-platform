# Request budgets for production question endpoints

> **For agentic workers:** Use superpowers:subagent-driven-development, with specification review followed by code-quality review. Use TDD and `uv run --no-sync`; no real model requests. The user approved implementation and commit/push after the design explanation.

**Goal:** Give each `/ask`, `/ask/stream`, `/agent/run`, and `/agent/run/stream` request an independent shared budget that bounds model attempts, output and context, enforces a deadline, and terminates explicitly rather than retrying/falling back after exhaustion.

**Architecture:** A thread-safe mutable budget travels through a ContextVar established by pure ASGI middleware for the complete response lifecycle. Permanent sync/async HTTP transports enforce each actual model attempt, including calls inside tools. Existing Agent/RAG boundaries propagate budget exceptions and check the deadline before additional work or cache writes. Application-owned HTTP pools are shared across requests and closed at application shutdown; requests never patch global configuration or close shared pools.

**Tech Stack:** Existing Python 3.11, FastAPI/Starlette, httpx, LangChain, anyio, pytest, standard library. No dependencies or Redis daily-user quota are added.

## Contract and acceptance

- Configurable defaults: 90 seconds per question request; six model HTTP attempts; 4096 cumulative output tokens; 1024 maximum output per request; 120000 cumulative serialized context bytes and 32000 per request. Context means canonical JSON UTF-8 messages+tools, not tokenizer counts. Keep existing Agent four-round limit.
- Reserve output before forwarding; settle only reliable provider completion usage. Failed/missing usage retains reservation. Streaming stays incremental. Never add temperature for kimi. Defaults retain existing thinking settings.
- BudgetExceeded has safe stable codes and sticky failure. Helpers: `get_request_budget()`, `check_request_budget()`, `request_budget_scope(budget)`; RequestBudget exposes `check()`, `stop(code)`, `reserve(body)`/settlement, `remaining_calls`, `can_start_tool(model_calls)` and a serializable snapshot. Exact successful consumption is allowed; only additional work is refused.
- SDK retries and HTTP transport retries are zero for protected HTTP calls; generation/retrieval retry loops perform zero retries inside a protected scope. Unscoped CLI entry points retain their explicit evaluation budgets/behavior. Do not globally mutate config at runtime.
- Pure ASGI middleware owns full response lifetime, creates one budget per protected POST, supervises an absolute deadline even while no token arrives, stops background follow-up work, and resets binding in all exit paths. It must not await a stuck worker forever. It cannot kill a remote request or an already running synchronous thread; document this without claiming A3.4/concurrency is solved.
- Before response headers: safe JSON error, timeout 504, other budget exhaustion 503. After streaming starts: protocol-compatible `error` then failed `done`, no successful terminal event afterwards. Use an app-specific stream-error encoder to avoid a rag-to-agent import dependency. Preserve CORS/request-id behavior and existing successful schemas.
- Context is propagated to existing anyio tool threads. Every ordinary-exception handler in affected generation, retrieval, planning, tool and fallback paths must propagate budget exhaustion. Check after downstream work and immediately before cache write. Cancellation must also prevent subsequent subquestions/model calls and cache publication.
- Agent checks available attempts/output before starting a tool, including the rule-decomposed subquestion count, and leaves capacity for final synthesis. If a tool is skipped after a tool call was returned, acknowledge every tool-call ID with a ToolMessage before final synthesis. With no protected scope, keep existing behavior. This applies to sync, sync-stream, and async-stream loops.
- No changes to evaluation datasets, prompts, Judge rubric, authentication, Redis quotas, concurrency semaphores, document versions or unrelated frontend behavior.

## Task 1: budget and HTTP enforcement

Files: new `rag/src/rag_app/infrastructure/request_budget.py`, `model_transport.py`; new matching tests in `rag/tests/infrastructure/`.

- [x] Write failing fake-clock/thread-counter tests for seventh-call refusal, independent scopes, exact-limit completion, stop propagation, context/output caps and conservative failed usage.
- [x] Implement RequestBudget with Lock, ContextVar scope with reset, immutable limits and safe exception. Default limits are configurable by the middleware, not mutated shared globals.
- [x] Write failing MockTransport tests with real ChatOpenAI invoke/stream/astream for blocked-before-forward, concurrent request isolation, zero hidden retry, output cap rewriting, JSON and SSE usage, deadline between chunks, response closure, and no-scope pass-through.
- [x] Implement synchronous/asynchronous guard transports and application-owned client bundle/binding. Keep common request/response accounting small and transport-specific streaming wrappers separate. Native responses and SSE usage must be observed without eagerly consuming the stream.
- [x] Run `uv run --no-sync pytest rag/tests/infrastructure/test_request_budget.py rag/tests/infrastructure/test_model_transport.py -q`; spec review, then quality review; resolve findings before committing.

Expected usage in tests:

```python
budget = RequestBudget(max_calls=1)
with request_budget_scope(budget):
    # Test uses a fake HTTP model, never a remote model.
    model.invoke("first")
    with pytest.raises(Exception):
        model.invoke("second")
assert sent_requests == 1
```

## Task 2: lifecycle, factories and service propagation

Files: new `request_budget_middleware.py` and tests; existing `llm_client.py`, `resources.py` only if required, both app `main.py`, RAG `ask_service.py`, Agent `service.py`, `streaming_service.py`, `planner.py`, `executor.py`, cancellation checks, stream schemas and tests.

- [x] Write failing ASGI tests for deadline before first token, deadline after a partial token, synchronous worker exceeding deadline, independent concurrent scopes, cleanup, and request-id/CORS on generated failures.
- [x] Implement middleware with bounded supervisor lifetime and gated sends. Use existing RAG/Agent stream encoders. Close abandoned async event generators; mark the shared budget stopped so surviving workers cannot start further work. Consume background task exceptions without hiding active failures.
- [x] Bind the app-owned model HTTP client bundle in the middleware; construct RAG startup resources with the same bundle; Agent get_client reads the active bundle. Close both clients during app shutdown. Unprotected/non-HTTP behavior remains explicit.
- [x] Write failing service tests proving a swallowed downstream budget failure cannot reach fallback or successful cache writes; then add checks and dedicated exception propagation. Effective retries inside a protected scope are zero, regardless of original configuration; transport-level attempts remain the final enforcement point.
- [x] Extend Agent error code literals to explicit budget codes; keep done termination_reason `failed`. RAG failure done adds `status=failed` without changing successful payloads.
- [x] Run targeted middleware, RAG service, request-context, Agent route and cancellation tests, then full regression. Spec review, then quality review and coherent commit/push.

## Task 3: final-answer capacity and end-to-end verification

Files: Agent `executor.py`, `loop.py`, `streaming.py`, `async_streaming.py`, tests and API documentation.

- [x] Write failing sync/sync-stream/async-stream tests that a tool requiring more than remaining calls minus one is skipped, every tool-call ID is acknowledged, and one final response can still be generated.
- [x] Implement a shared small tool-cost estimator using the existing rule decomposer. Unknown tools do not receive speculative capabilities. Apply preflight in all loops while preserving unscoped execution and existing four-round behavior.
- [x] Exercise all four actual API paths with real ChatOpenAI plus fake local HTTP/SSE transports; assert actual forwarded counts, output reservations, per-request isolation and timeout error schemas. Cover API-provided shared RAG resources, not just newly constructed clients.
- [x] Verify a surviving tool thread cannot start a second subquestion/model call or publish cache after response failure; do not claim the original thread/remote request was forcibly terminated.
- [x] Run `uv run --no-sync pytest -q`, CLI preview/help, `git diff --check`; review complete change, update local roadmap A3.1/A3.2 truthfully, document defaults/env knobs and limitations, commit/push and fast-forward main if unchanged.

## Verification commands

```bash
uv sync --locked
uv run --no-sync pytest -q
uv run --no-sync python -m agent_app.scripts.compare_strategies
git diff --check
```

Success requires all four protected endpoints and nested real code paths to be bounded. No real model run is necessary or authorized; all model/embedding/vector operations in verification use fakes or local MockTransport.

## Completion evidence (2026-10-26)

- TDD reproduced swallowed budget exceptions, late cache publication, insufficient final-answer capacity, gzip usage misaccounting, fixed output cap, slow-send deadlocks, send-cleanup overlap and initial/final deadline exception gaps before each fix.
- Independent Task 1 and Task 2/3 specification and quality reviews approved after fixes.
- Complete offline regression: 478 Python tests passed with two pre-existing dependency warnings; frontend 27 tests, ESLint and default Next.js production build passed. CLI preview/help and document links passed; no real model calls or new dependencies.
- Core implementation: b05632b. Endpoint lifecycle and Agent implementation: 31d0601. Defaults, error protocols, test coverage and remaining A3.3/A3.4 boundaries are documented in docs/request-budgets.md.
