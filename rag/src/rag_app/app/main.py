import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rag_app.app.routers.ask import router as ask_router
from rag_app.app.routers.documents import router as document_router
from rag_app.app.routers.health import router as health_router
from rag_app.app.routers.prompt_eval import router as prompt_eval_router
from rag_app.infrastructure.request_context import (
    RequestIdMiddleware,
    configure_request_logging,
)
from rag_app.infrastructure.resources import create_app_resources

configure_request_logging(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.resources = create_app_resources()
    yield


app = FastAPI(lifespan=lifespan)

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
