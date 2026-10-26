import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware

from rag_app.infrastructure.request_context import (
    RequestIdMiddleware,
    configure_request_logging,
)

from agent_app.app.routers.health import router as health_router
from agent_app.app.routers.run import router as run_router
from agent_app.app.routers.tools import router as tools_router
from agent_app.streaming_service import encode_budget_failure
from rag_app.infrastructure.model_transport import model_clients
from rag_app.infrastructure.request_budget_middleware import RequestBudgetMiddleware

configure_request_logging(level=logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with model_clients() as clients:
        app.state.model_clients = clients
        try:
            yield
        finally:
            del app.state.model_clients


app = FastAPI(lifespan=lifespan)

app.add_middleware(RequestBudgetMiddleware, paths={"/agent/run", "/agent/run/stream"},
                   stream_error=encode_budget_failure)
app.add_middleware(RequestIdMiddleware)

app.add_middleware(
      CORSMiddleware,
      allow_origins=["http://localhost:3000"],
      allow_methods=["*"],
      allow_headers=["*"],
  )

app.include_router(health_router)
app.include_router(run_router)
app.include_router(tools_router)
