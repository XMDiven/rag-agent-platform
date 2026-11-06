import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from rag_app.infrastructure.resources import AppResources
from rag_app.schemas.answer_schema import AnswerResponse
from rag_app.schemas.ask_schema import AskRequest
from rag_app.services.ask_service import ask_question, stream_ask_question
from rag_app.infrastructure.request_concurrency import admit_request
from rag_app.infrastructure.tracked_work import run_sync_tracked, iterate_sync_tracked
from rag_app.infrastructure.request_budget import request_failure_message

router = APIRouter()


def encode_stream_event(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def encode_budget_failure(code: str) -> bytes:
    return (encode_stream_event({"type": "error", "error_type": "BudgetExceeded",
                                 "code": code, "message": request_failure_message(code)})
            + encode_stream_event({"type": "done", "status": "failed"})).encode()


def stream_response_events(
    question: str,
    resources: AppResources,
) -> Iterator[str]:
    for event in stream_ask_question(question=question, resources=resources):
        yield encode_stream_event(event)


@router.post("/ask", response_model=AnswerResponse)
async def ask(request: Request, body: AskRequest) -> AnswerResponse:
    await admit_request()
    result = await run_sync_tracked(ask_question,
        body.question,
        resources=request.app.state.resources,
    )
    return AnswerResponse(**result)


@router.post("/ask/stream")
async def ask_stream(request: Request, body: AskRequest) -> StreamingResponse:
    await admit_request()
    return StreamingResponse(
        iterate_sync_tracked(stream_response_events(
            question=body.question,
            resources=request.app.state.resources,
        )),
        media_type="application/x-ndjson",
    )
