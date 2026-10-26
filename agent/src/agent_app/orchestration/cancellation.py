"""Cooperative cancellation for synchronous tools running in a worker thread."""

from asyncio import CancelledError
from contextvars import ContextVar
from threading import Event
from rag_app.infrastructure.request_budget import check_request_budget

cancel_event: ContextVar[Event | None] = ContextVar("agent_cancel_event", default=None)


def check_cancelled() -> None:
    check_request_budget()
    event = cancel_event.get()
    if event is not None and event.is_set():
        # BaseException: normal tool failure handlers must not retry cancellation.
        raise CancelledError()
