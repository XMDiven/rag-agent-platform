# Agent BFF and Steps Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route browser questions through a server-only Next.js BFF to `/agent/run` and make the Agent loop's real multi-round `steps` visible in the demo UI.

**Architecture:** A Next.js Route Handler owns the upstream Agent URL, validates the browser request, forwards it to FastAPI, and preserves the upstream JSON/status without exposing infrastructure addresses to the client. The existing client page calls the same-origin route and renders the Agent response, including termination reason, selected tool, sources, and per-round tool steps. Agent streaming remains out of scope and will be designed separately.

**Tech Stack:** Next.js 16 App Router, TypeScript, Node built-in test runner, FastAPI Agent API, Docker Compose.

---

### Task 1: Add the server-only Agent BFF

**Files:**
- Create: `frontend/tests/agent-route.test.ts`
- Create: `frontend/app/api/agent/route.ts`
- Modify: `frontend/package.json`

- [x] **Step 1: Write failing Route Handler tests**

Cover three observable behaviors with Node's built-in test runner: a valid question is forwarded to `AGENT_API_URL`, malformed or blank input returns 400 without calling upstream, and an upstream network failure returns a stable 502 JSON response.

- [x] **Step 2: Run tests and verify RED**

Run: `cd frontend && node --test --experimental-strip-types tests/agent-route.test.ts`

Expected: FAIL with `ERR_MODULE_NOT_FOUND` because `app/api/agent/route.ts` does not exist.

- [x] **Step 3: Implement the minimal Route Handler**

Implement `POST(request: Request): Promise<Response>` using standard Web APIs. Read `AGENT_API_URL` at request time with local fallback `http://localhost:8002/agent/run`, validate `{ question: string }`, forward JSON with `cache: "no-store"`, preserve upstream status/content type/body, and convert fetch failures to `{ "error": "Agent 服务暂时不可用" }` with status 502.

- [x] **Step 4: Add the frontend test command and verify GREEN**

Add `"test": "node --test --experimental-strip-types tests/*.test.ts"` to `frontend/package.json`.

Run: `cd frontend && npm test`

Expected: all Route Handler tests pass.

### Task 2: Render Agent orchestration evidence in the page

**Files:**
- Create: `frontend/tests/agent-view-model.test.ts`
- Create: `frontend/app/agent-view-model.ts`
- Modify: `frontend/app/page.tsx`

- [x] **Step 1: Write failing step view-model tests**

Define expected display behavior for a tool round and a final-answer round: round number is retained, snake-case statuses become readable labels, missing tools display `final answer`, and tool arguments are serialized only when present.

- [x] **Step 2: Run tests and verify RED**

Run: `cd frontend && node --test --experimental-strip-types tests/agent-view-model.test.ts`

Expected: FAIL with `ERR_MODULE_NOT_FOUND` because `app/agent-view-model.ts` does not exist.

- [x] **Step 3: Implement the typed Agent view model**

Define `AgentStep`, `AgentRunResponse`, and `AgentStepViewModel`, plus `buildAgentStepViewModel(step)` with no React dependency so the transformation is independently testable.

- [x] **Step 4: Replace direct RAG access with the same-origin BFF**

Update `page.tsx` to POST to `/api/agent`, type the response as `AgentRunResponse`, rename the result labels from RAG trace to Agent orchestration, show selected tool and termination reason, and render every `steps` entry with round/status/tool/arguments. Keep existing loading, error, source, and responsive behavior.

- [x] **Step 5: Verify unit tests, lint, and TypeScript**

Run: `cd frontend && npm test && npm run lint && npx tsc --noEmit`

Expected: all commands exit 0.

### Task 3: Wire runtime configuration and document the boundary

**Files:**
- Modify: `compose.yaml`
- Modify: `README.md`
- Modify: `resume_alignment.md` if present in the local checkout; do not stage it because it is intentionally ignored

- [x] **Step 1: Configure the frontend's server-only upstream**

Set `AGENT_API_URL=http://agent-api:8002/agent/run` on the Compose frontend service and make its health dependency target `agent-api`. Document the safe local `AGENT_API_URL` command without putting the server-only value in a browser-visible variable.

- [x] **Step 2: Update documentation without claiming streaming**

Document that the browser calls a same-origin BFF and that the UI exposes Agent loop steps. Split local `resume_alignment.md` B-2 into completed B-2a and pending B-2b Agent streaming so the source of truth matches the implementation.

- [x] **Step 3: Run complete verification**

Run:

```bash
cd frontend && npm test && npm run lint && npx tsc --noEmit && npm run build
cd ../rag && conda run --no-capture-output -n AI_DEV pytest tests/ -q
cd ../agent && conda run --no-capture-output -n AI_DEV pytest tests/ -q
cd .. && docker compose --env-file .env.example config --quiet
git diff --check
```

Expected: frontend tests/lint/type-check/build pass, RAG reports 125 passing tests, Agent reports 53 passing tests, Compose validates, and `git diff --check` exits 0.

- [x] **Step 4: Review scope, commit, and push**

Inspect `git status -sb`, `git diff --stat`, and the complete diff. Stage only the plan, frontend BFF/UI/tests, Compose environment wiring, and README changes. Commit with `feat: expose agent steps through frontend BFF`, then push `codex/bff-agent-steps` to `origin` with upstream tracking.
