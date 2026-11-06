# Request Concurrency Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development, TDD, specification review followed by quality review. User approved implementation in this chat. No real model requests.

**Goal:** Bound simultaneous question work and queue waiting while retaining permits for cancelled requests whose local workers are still running.

**Architecture:** Existing budget middleware establishes a per-request work record. Async route adapters acquire a lifespan-owned limiter before business execution. Tracked sync workers and bounded streaming production report actual completion; permits release after app, response and workers finish.

**Tech Stack:** Existing FastAPI, asyncio, AnyIO, ContextVar, threading locks, pytest, httpx MockTransport. Python through `uv run --no-sync`. No dependencies.

## Task 1: limiter and work tracking

Create `rag/src/rag_app/infrastructure/request_concurrency.py`, `tracked_work.py`, and matching infrastructure tests.

- [x] Write failing 20/4 Event-driven tests for bounded active/waiting counts, queue rejection/timeout, acquisition cancellation, exceptions, close, no-wait mode and idempotent permit release.
- [x] Implement `RequestLimiter(max_active=4,max_waiting=16,wait_timeout_seconds=5)`, async `acquire()`, `close()`, `snapshot()`, idempotent permit `release()`. Admission rejection extends existing `BudgetExceeded` with queue/service codes.
- [x] Implement `RequestWork(limiter)` with async `admit()`, `app_finished()`, `response_finished()` and worker ownership. Context helpers `request_work_scope(work)`, `get_request_work()`, `admit_request()`; no scope admission is a no-op for internal callers. Preserve sticky budget on admission failure.
- [x] Write failing cancellation-before-start, cancelled-waiter/blocked-thread, cleanup and output-stream tests. Implement `run_sync_tracked(func,*args,**kwargs)` and `iterate_sync_tracked(iterable)` with retained worker completion, ContextVar propagation, bounded channel and same-thread iterator cleanup. Do not free a permit from an abandoned awaiter's finally.
- [x] Run core tests, then independent spec and quality reviews; resolve findings. Commit verified core separately.

## Task 2: middleware and actual route integration

Modify budget middleware, config, both main lifespans, four question route adapters, Agent async tool execution, error encoders/schema, frontend parser only if needed. Add `agent/tests/api/test_request_concurrency_api.py` plus middleware integration tests.

- [x] Write failing fake ASGI/actual API tests for queued admission, headers not started while queued, overflow/queue timeout, total deadline including queue, queued disconnect, CORS/request-id, normal/failed whole-response completion and retained threads.
- [x] Add settings `request_max_concurrent=4`, `request_max_waiting=16`, `request_queue_timeout_seconds=5`; create fresh limiter in each application lifespan, close admission on shutdown. Middleware fallback supports existing lifespan-less TestClient tests without process-global asyncio objects.
- [x] Bind one RequestWork inside execute; app task done callback calls app_finished; outer finally calls response_finished. Acquire only inside async endpoint after validation via `await admit_request()`. Use tracked sync adapters for JSON business, RAG stream and async Agent tools. Nested tools never reacquire.
- [x] Queue failures use safe messages and stable 503 codes. Extend Agent backend/frontend schema compatibility; frontend HTTP-error reader recognizes existing nested budget error.message. Keep successful response shapes and CLI unchanged.
- [x] Run targeted core/API/budget/cancellation suites, then complete Python regression and frontend test/lint/build; spec and quality review before integration.

## Task 3: docs and delivery

- [x] Document defaults, request/worker lifetime, per-worker scope, observed cancellation behavior, provider/shutdown limits and offline test evidence in `docs/request-budgets.md`, README and local ignored roadmap.
- [x] Verify CLI preview/help, `git diff --check`, complete review; commit coherent changes, push, fast-forward main if unchanged and verify there. Preserve user-owned `.agents/`, `openspec/` and ignored credentials. Archive the managed worktree once needed ignored files are preserved in the primary workspace.

## Acceptance examples

```text
20 requests, max_active=4, max_waiting=16:
  four controlled workers start; 16 wait; releasing workers admits queued work;
  measured active work <=4 throughout and returns to zero.
cancel during a blocked local model/tool:
  HTTP ends; active remains occupied; another question cannot enter;
  after local work exits and cleanup finishes, exactly one permit returns.
```

Run `uv run --no-sync pytest -q`, CLI preview/help; if frontend changed, run `npm test`, `npm run lint`, `npm run build` there. All model/vector/cache behavior in tests is fake/local.

## Completion evidence — 2026-11-06

- Baseline: 478 Python tests passed. Core implementation c8ce0f8; API integration 7342044.
- TDD reproduced missing admission/lifecycle, budget priority during queue timeout, pre-dispatch iterator errors, unsupported frontend queue codes and structured error messages before fixes.
- Independent core and integration spec/quality reviews and a final holistic review approved.
- Final Python regression: 518 passed, two existing dependency deprecation warnings. Frontend: 28 tests, lint and default Next.js production build passed. CLI preview/help, document links and diff whitespace checks passed. No real model calls or new dependencies.
- Old sync-handler inspection test now verifies actual worker execution. Legacy lifespan-less Agent tests isolate application state at test boundaries; production shutdown still rejects HTTP requests and remains directly tested.
- A3.4 completion is limited to observed local workers and app/response cleanup. Provider work after connection close remains unknowable, and shutdown admission closure is not a durable queue or indefinite worker drain.
