import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from rag_app.infrastructure.resources import AppResources
from rag_app.schemas.answer_schema import AnswerResponse
from rag_app.schemas.ask_schema import AskRequest
from rag_app.services.ask_service import ask_question, stream_ask_question

router = APIRouter()


def encode_stream_event(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def encode_budget_failure(code: str) -> bytes:
    return (encode_stream_event({"type": "error", "error_type": "BudgetExceeded",
                                 "code": code, "message": "请求超过执行预算，请缩小问题后重试。"})
            + encode_stream_event({"type": "done", "status": "failed"})).encode()


def stream_response_events(
    question: str,
    resources: AppResources,
) -> Iterator[str]:
    for event in stream_ask_question(question=question, resources=resources):
        yield encode_stream_event(event)


@router.post("/ask", response_model=AnswerResponse)
def ask(request: Request, body: AskRequest) -> AnswerResponse:
    result = ask_question(
        body.question,
        resources=request.app.state.resources,
    )
    return AnswerResponse(**result)


@router.post("/ask/stream")
async def ask_stream(request: Request, body: AskRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_response_events(
            question=body.question,
            resources=request.app.state.resources,
        ),
        media_type="application/x-ndjson",
    )
