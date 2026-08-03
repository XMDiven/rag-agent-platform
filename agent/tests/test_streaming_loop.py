import json

import pytest
from langchain_core.messages import AIMessageChunk

from agent_app.orchestration.executor import ToolResult
from agent_app.orchestration.streaming import (
    MixedModelOutputError,
    stream_agent_loop,
)


def tool_chunks(
    name: str = "retrieval_tool",
    question: str = "What is RAG?",
) -> list[AIMessageChunk]:
    return [
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": name,
                    "args": '{"ques',
                    "id": "call_1",
                    "index": 0,
                    "type": "tool_call_chunk",
                }
            ],
        ),
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": None,
                    "args": f'tion":"{question}"}}',
                    "id": None,
                    "index": 0,
                    "type": "tool_call_chunk",
                }
            ],
        ),
    ]


def successful_tool_result(
    name: str,
    source: str = "rag.md",
) -> ToolResult:
    return ToolResult(
        tool_name=name,
        status="success",
        output={
            "answer": "context",
            "sources": [{"source": source}],
        },
        attempts=[{"attempt": 1, "status": "success"}],
    )


def test_stream_agent_loop_emits_real_answer_chunks(
    make_streaming_llm,
) -> None:
    llm = make_streaming_llm(
        [[AIMessageChunk(content="RAG "), AIMessageChunk(content="answer")]]
    )

    events = list(
        stream_agent_loop(
            question="What is RAG?",
            llm=llm,
            execute_tool=lambda name, args: None,
            max_steps=4,
        )
    )

    assert [event.type for event in events] == [
        "answer_delta",
        "answer_delta",
        "sources",
        "done",
    ]
    assert "".join(
        event.data.text
        for event in events
        if event.type == "answer_delta"
    ) == "RAG answer"
    assert events[-1].data.termination_reason == "final_answer"
    assert llm.tool_choices == ["auto"]


def test_stream_agent_loop_executes_assembled_tool_once(
    make_streaming_llm,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    llm = make_streaming_llm(
        [
            tool_chunks(),
            [AIMessageChunk(content="Grounded answer")],
        ]
    )

    def execute(name, args):
        calls.append((name, args))
        return successful_tool_result(name)

    events = list(stream_agent_loop("What is RAG?", llm, execute))

    assert calls == [
        ("retrieval_tool", {"question": "What is RAG?"})
    ]
    assert [event.type for event in events] == [
        "step",
        "answer_delta",
        "sources",
        "done",
    ]
    assert events[2].data.sources == [{"source": "rag.md"}]
    assert events[3].data.selected_tool == "retrieval_tool"


def test_stream_agent_loop_hides_tool_error_and_allows_recovery(
    make_streaming_llm,
) -> None:
    llm = make_streaming_llm(
        [
            tool_chunks(),
            [AIMessageChunk(content="Recovered answer")],
        ]
    )

    def fail_tool(name, args):
        raise RuntimeError("api_key=secret")

    events = list(stream_agent_loop("What is RAG?", llm, fail_tool))
    serialized = "\n".join(event.model_dump_json() for event in events)

    assert events[0].type == "step"
    assert events[0].data.status == "tool_failed"
    assert [event.type for event in events][-3:] == [
        "answer_delta",
        "sources",
        "done",
    ]
    assert "api_key" not in serialized
    assert "secret" not in serialized


def test_stream_agent_loop_rejects_mixed_answer_and_tool_output(
    make_streaming_llm,
) -> None:
    mixed_chunks = [
        AIMessageChunk(content="partial"),
        *tool_chunks(),
    ]
    llm = make_streaming_llm([mixed_chunks])

    with pytest.raises(MixedModelOutputError):
        list(stream_agent_loop("What is RAG?", llm, lambda name, args: None))


def test_stream_agent_loop_streams_forced_answer_after_max_steps(
    make_streaming_llm,
) -> None:
    llm = make_streaming_llm(
        [
            tool_chunks(),
            [
                AIMessageChunk(content="Forced "),
                AIMessageChunk(content="answer"),
            ],
        ]
    )

    events = list(
        stream_agent_loop(
            "What is RAG?",
            llm,
            lambda name, args: successful_tool_result(name),
            max_steps=1,
        )
    )

    assert llm.tool_choices == ["auto", "none"]
    assert [event.type for event in events] == [
        "step",
        "answer_delta",
        "answer_delta",
        "sources",
        "done",
    ]
    assert events[-1].data.termination_reason == "max_steps"
    assert json.loads(events[-1].model_dump_json())["data"][
        "tool_status"
    ] == "success"
