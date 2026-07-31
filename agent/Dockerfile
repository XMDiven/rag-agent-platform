# syntax=docker/dockerfile:1
# 构建上下文为仓库根目录，需要 uv workspace 的锁文件

FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_HTTP_TIMEOUT=180 \
    UV_CONCURRENT_DOWNLOADS=8

WORKDIR /app

# 只装第三方依赖，源码变动不会让这一层失效
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=rag/pyproject.toml,target=rag/pyproject.toml \
    --mount=type=bind,source=agent/pyproject.toml,target=agent/pyproject.toml \
    uv sync --frozen --no-dev --no-install-workspace --package agent-app

COPY uv.lock pyproject.toml ./
COPY rag ./rag
COPY agent ./agent

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package agent-app

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/rag /app/rag
COPY --from=builder /app/agent /app/agent

EXPOSE 8002

CMD ["uvicorn", "agent_app.app.main:app", "--host", "0.0.0.0", "--port", "8002"]
