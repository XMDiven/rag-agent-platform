import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rag_app.app.routers.ask import router as ask_router
from rag_app.app.routers.ask import encode_budget_failure
from rag_app.app.routers.documents import router as document_router
from rag_app.app.routers.health import router as health_router
from rag_app.app.routers.prompt_eval import router as prompt_eval_router
from rag_app.infrastructure.request_context import (
    RequestIdMiddleware,
    configure_request_logging,
)
from rag_app.infrastructure.resources import create_app_resources
from rag_app.infrastructure.model_transport import model_clients, model_clients_scope
from rag_app.infrastructure.request_budget_middleware import RequestBudgetMiddleware

configure_request_logging(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with model_clients() as clients:
        app.state.model_clients = clients
        with model_clients_scope(clients):
            app.state.resources = create_app_resources()
        try:
            yield
        finally:
            del app.state.model_clients


app = FastAPI(lifespan=lifespan)

app.add_middleware(RequestBudgetMiddleware, paths={"/ask", "/ask/stream"},
                   stream_error=encode_budget_failure)
app.add_middleware(RequestIdMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ask_router)
app.include_router(health_router)
app.include_router(document_router)
app.include_router(prompt_eval_router)
