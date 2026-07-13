# One-Command Docker Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make a fresh clone start the frontend, RAG API, Agent API, and Qdrant with one Docker Compose command while using Ollama from the host machine.

**Architecture:** Keep the existing service-specific backend Dockerfiles and add a production frontend image. A root Compose file builds all application services, injects runtime configuration from an ignored root `.env`, connects both Python services to Qdrant by Compose DNS, and reaches host Ollama through `host.docker.internal`.

**Tech Stack:** Docker Compose, Docker multi-stage builds, Next.js 16, FastAPI, Qdrant, host-managed Ollama.

---

### Task 1: Define the deployment contract

**Files:**
- Create: `.env.example`
- Create: `compose.yaml`
- Create: `rag/src/rag_app/scripts/ensure_collection.py`
- Create: `rag/tests/scripts/test_ensure_collection.py`
- Modify: `rag/Dockerfile`
- Modify: `agent/Dockerfile`

- [x] **Step 1: Verify the deployment entrypoint is missing**

Run `test -f compose.yaml` and `test -f .env.example` from the repository root.

Expected: both checks fail because the root deployment files do not exist yet.

- [x] **Step 2: Add the environment contract**

Create a root `.env.example` containing safe non-secret defaults for Qdrant collection/search settings, host Ollama embedding configuration, Moonshot-compatible LLM configuration, and placeholder API keys. Do not copy or commit the real `rag/.env`.

- [x] **Step 3: Add root service orchestration**

Create `compose.yaml` with `frontend`, `rag-api`, `agent-api`, `qdrant`, and a one-shot `qdrant-init` service. Build the backends from their existing Dockerfiles, override `QDRANT_URL` with `http://qdrant:6333`, map host Ollama on Linux with `host-gateway`, add health checks, and persist Qdrant and uploaded RAG data in named volumes.

Configure the existing backend dependency-install layers with a 120-second pip read timeout and five retries so transient slow package downloads do not abort a fresh image build.

Test and implement an idempotent collection initializer that keeps an existing collection unchanged and creates a missing collection with the active embedding model's vector dimension. Make both APIs depend on successful initializer completion.

- [x] **Step 4: Validate the Compose model**

Run `docker compose --env-file .env.example config --quiet`.

Expected: exit code 0 with no interpolation or schema errors.

### Task 2: Build the frontend image

**Files:**
- Create: `frontend/Dockerfile`
- Create: `frontend/.dockerignore`
- Modify: `frontend/next.config.ts`

- [x] **Step 1: Verify the frontend image contract is missing**

Run `test -f frontend/Dockerfile` and `test -f frontend/.dockerignore`.

Expected: both checks fail because the frontend has no container build definition.

- [x] **Step 2: Enable standalone Next.js output**

Set `output: "standalone"` in `frontend/next.config.ts` so the runtime image contains only traced production dependencies.

- [x] **Step 3: Add a multi-stage, non-root frontend image**

Use Node 22 Alpine stages to install locked dependencies with `npm ci`, build the Next.js application, copy standalone/static/public output, and run `server.js` as an unprivileged user on port 3000.

- [x] **Step 4: Exclude local build artifacts**

Add `frontend/.dockerignore` entries for `node_modules`, `.next`, local environment files, logs, and editor metadata.

- [x] **Step 5: Build the frontend image**

Run `docker compose --env-file .env.example build frontend`.

Expected: the Next.js production build and final runtime image complete successfully.

### Task 3: Document and verify the workflow

**Files:**
- Modify: `README.md`

- [x] **Step 1: Document prerequisites and the one-command path**

Add a root quick-start section covering Docker, host Ollama, `ollama pull nomic-embed-text`, `.env.example` copying, API key configuration, `docker compose up --build`, service URLs, health checks, shutdown, and the empty-index boundary.

- [x] **Step 2: Run repository verification**

Run:

- `docker compose --env-file .env.example config --quiet`
- `docker compose --env-file .env.example build`
- `cd rag && conda run -n AI_DEV pytest tests/ -q`
- `cd agent && conda run -n AI_DEV pytest tests/ -q`
- `npm run lint --prefix frontend`
- `npm run build --prefix frontend`

Expected: all commands exit 0.

- [x] **Step 3: Review the final diff**

Inspect `git diff --check`, `git diff --stat`, every changed file, and the rendered Compose model. Verify no real secret, local data, unrelated refactor, or unsupported deployment claim is included.

- [x] **Step 4: Re-run verification after review fixes**

Repeat all verification commands affected by any review change and confirm they exit 0.

- [x] **Step 5: Commit the deployment feature**

Stage only the deployment files and commit with `feat: add one-command Docker deployment`. Do not add an AI attribution trailer.
