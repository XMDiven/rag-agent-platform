import json

import pytest
from pydantic import ValidationError

from agent_app.schemas.stream import (
    AnswerDeltaData,
    AnswerDeltaEvent,
    DoneData,
    DoneEvent,
    ErrorData,
    ErrorEvent,
    SourcesData,
    SourcesEvent,
    StepData,
    StepEvent,
    encode_event,
)


def test_encode_step_event_as_one_ndjson_line() -> None:
    event = StepEvent(
        data=StepData(
            round=1,
            status="tool_executed",
            tool_name="retrieval_tool",
            tool_args={"question": "什么是 RAG？"},
            tool_status="success",
        )
    )

    encoded = encode_event(event)

    assert encoded.endswith("\n")
    assert encoded.count("\n") == 1
    assert json.loads(encoded) == {
        "version": 1,
        "type": "step",
        "data": {
            "round": 1,
            "status": "tool_executed",
            "tool_name": "retrieval_tool",
            "tool_args": {"question": "什么是 RAG？"},
            "tool_status": "success",
        },
    }


def test_terminal_events_have_stable_types() -> None:
    events = [
        AnswerDeltaEvent(data=AnswerDeltaData(text="RAG")),
        SourcesEvent(data=SourcesData(sources=[{"source": "rag.md"}])),
        ErrorEvent(
            data=ErrorData(
                code="agent_stream_failed",
                message="Agent 流式执行失败，请重试",
            )
        ),
        DoneEvent(
            data=DoneData(
                termination_reason="final_answer",
                selected_tool="retrieval_tool",
                tool_status="success",
            )
        ),
    ]

    assert [json.loads(encode_event(event))["type"] for event in events] == [
        "answer_delta",
        "sources",
        "error",
        "done",
    ]


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (AnswerDeltaData, {"text": ""}),
        (
            StepData,
            {
                "round": 0,
                "status": "tool_executed",
                "tool_name": "retrieval_tool",
                "tool_status": "success",
            },
        ),
    ],
)
def test_event_data_rejects_invalid_boundaries(model, kwargs) -> None:
    with pytest.raises(ValidationError):
        model(**kwargs)
