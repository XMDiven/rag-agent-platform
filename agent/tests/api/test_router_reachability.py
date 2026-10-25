import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from rag_app.config import config

from agent_app.app.main import app
from agent_app.orchestration import planner
from agent_app.orchestration.tool_selector import ToolSelection
from agent_app.tools import get_tool


def test_normal_run_loop_does_not_call_finetuned_router(
    monkeypatch,
    patch_loop_llm,
) -> None:
    monkeypatch.setattr(config, "ROUTER_BACKEND", "finetuned")
    patch_loop_llm([AIMessage(content="fake answer")])

    router_calls: list[str] = []

    def fake_router(question: str) -> ToolSelection:
        router_calls.append(question)
        return ToolSelection(
            tool=get_tool("fallback_tool"),
            tool_args={},
            reason="fake router selection",
        )

    monkeypatch.setattr(planner, "select_tool_with_finetuned_router", fake_router)

    response = TestClient(app).post(
        "/agent/run",
        json={"question": "What is RAG?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "fake answer"
    assert data["termination_reason"] == "final_answer"
    assert data["trace"][1]["step"] == "agent_loop"
    assert router_calls == []


def test_failed_run_loop_calls_finetuned_router_in_single_step_fallback(
    monkeypatch,
    patch_loop_llm,
) -> None:
    monkeypatch.setattr(config, "ROUTER_BACKEND", "finetuned")
    patch_loop_llm([])

    loop_calls: list[str] = []
    router_calls: list[str] = []

    def fail_loop(question, llm, execute_tool, max_steps):
        loop_calls.append(question)
        raise RuntimeError("forced loop failure")

    def fake_router(question: str) -> ToolSelection:
        router_calls.append(question)
        return ToolSelection(
            tool=get_tool("fallback_tool"),
            tool_args={},
            reason="fake router selection",
        )

    monkeypatch.setattr("agent_app.service.run_agent_loop", fail_loop)
    monkeypatch.setattr(planner, "select_tool_with_finetuned_router", fake_router)

    question = "What is RAG?"
    response = TestClient(app).post("/agent/run", json={"question": question})

    assert response.status_code == 200
    data = response.json()
    assert data["termination_reason"] == "single_step"
    assert data["selected_tool"] == "fallback_tool"
    assert [item["step"] for item in data["trace"]] == [
        "analyze_question",
        "plan_tool",
        "execute_tool",
    ]
    assert loop_calls == [question]
    assert router_calls == [question]


@pytest.fixture
def finetuned_router_calls(monkeypatch):
    monkeypatch.setattr(config, "ROUTER_BACKEND", "finetuned")
    calls: list[str] = []

    def fake_router(question: str) -> ToolSelection:
        calls.append(question)
        return ToolSelection(
            tool=get_tool("fallback_tool"),
            tool_args={},
            reason="fake router selection",
        )

    monkeypatch.setattr(planner, "select_tool_with_finetuned_router", fake_router)
    return calls


def test_normal_stream_loop_does_not_call_finetuned_router(
    monkeypatch,
    make_streaming_llm,
    finetuned_router_calls,
) -> None:
    model = make_streaming_llm(
        [[AIMessageChunk(content="RAG "), AIMessageChunk(content="answer")]]
    )
    monkeypatch.setattr("agent_app.streaming_service.get_client", lambda: model)

    response = TestClient(app).post(
        "/agent/run/stream", json={"question": "What is RAG?"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == [
        "answer_delta", "answer_delta", "sources", "done"
    ]
    assert all(event["version"] == 1 for event in events)
    assert "".join(event["data"]["text"] for event in events[:2]) == "RAG answer"
    assert events[2]["data"] == {"sources": []}
    assert events[-1]["data"]["termination_reason"] == "final_answer"
    assert events[-1]["data"]["tool_status"] == "success"
    assert len(model.invocations) == 1
    assert model.invocations[0][-1].content == "What is RAG?"
    assert model.tool_choices == ["auto"]
    assert finetuned_router_calls == []


@pytest.mark.parametrize("partial_answer", ["", "Partial answer"])
def test_failed_stream_loop_returns_error_without_finetuned_router(
    monkeypatch,
    make_streaming_llm,
    finetuned_router_calls,
    partial_answer: str,
) -> None:
    model = make_streaming_llm([])
    calls: list[list] = []

    async def failing_stream(messages):
        calls.append(list(messages))
        if partial_answer:
            yield AIMessageChunk(content=partial_answer)
        raise RuntimeError("private upstream failure")

    monkeypatch.setattr(model, "astream", failing_stream)
    monkeypatch.setattr("agent_app.streaming_service.get_client", lambda: model)

    response = TestClient(app).post(
        "/agent/run/stream", json={"question": "What is RAG?"}
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    expected_types = (["answer_delta"] if partial_answer else []) + ["error", "done"]
    assert [event["type"] for event in events] == expected_types
    assert all(event["version"] == 1 for event in events)
    if partial_answer:
        assert events[0]["data"]["text"] == partial_answer
    assert events[-2]["data"]["code"] == "agent_stream_failed"
    assert events[-1]["data"]["termination_reason"] == "failed"
    assert events[-1]["data"]["tool_status"] == "failed"
    assert "private upstream failure" not in response.text
    assert len(calls) == 1
    assert calls[0][-1].content == "What is RAG?"
    assert model.tool_choices == ["auto"]
    assert finetuned_router_calls == []


def test_stream_loop_passes_summary_to_next_model_round(
    monkeypatch,
    make_streaming_llm,
    finetuned_router_calls,
) -> None:
    text = "RAG retrieves context before generating answers."
    summary = "RAG grounds answers in retrieved context."
    model = make_streaming_llm(
        [
            [AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": "summary_tool",
                    "args": json.dumps({"text": text}),
                    "id": "call_summary_1",
                    "index": 0,
                }],
            )],
            [AIMessageChunk(content="Summarized answer")],
        ]
    )
    summary_model = Mock()
    summary_model.invoke.return_value = AIMessage(content=summary)
    monkeypatch.setattr("agent_app.streaming_service.get_client", lambda: model)
    monkeypatch.setattr(
        "agent_app.tools.summary.get_client", lambda: summary_model
    )

    response = TestClient(app).post(
        "/agent/run/stream", json={"question": f"Summarize: {text}"}
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == [
        "step", "answer_delta", "sources", "done"
    ]
    assert all(event["version"] == 1 for event in events)
    assert events[0]["data"]["tool_name"] == "summary_tool"
    assert events[0]["data"]["tool_status"] == "success"
    assert events[1]["data"]["text"] == "Summarized answer"
    assert events[2]["data"] == {"sources": []}
    assert events[-1]["data"] == {
        "termination_reason": "final_answer",
        "selected_tool": "summary_tool",
        "tool_status": "success",
    }
    summary_model.invoke.assert_called_once()
    assert summary_model.invoke.call_args.args[0][-1].content == text
    assert len(model.invocations) == 2
    tool_message = model.invocations[1][-1]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.tool_call_id == "call_summary_1"
    assert json.loads(str(tool_message.content)) == {
        "status": "success", "summary": summary, "sources": []
    }
    assert model.tool_choices == ["auto", "auto"]
    assert finetuned_router_calls == []


def test_http_stream_passes_original_evidence_to_final_model(
    monkeypatch,
    make_streaming_llm,
) -> None:
    source = {
        "source": "llamaindex.md",
        "section_path": "use cases",
        "snippet": "LlamaIndex supports document question answering.",
    }
    model = make_streaming_llm([
        [AIMessageChunk(content="", tool_call_chunks=[{
            "name": "retrieval_tool",
            "args": json.dumps({"question": "LlamaIndex use cases"}),
            "id": "retrieve_evidence",
            "index": 0,
        }])],
        [AIMessageChunk(content="Document question answering [1].")],
    ])
    monkeypatch.setattr("agent_app.streaming_service.get_client", lambda: model)
    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        lambda question: {"answer": "Generated summary.", "sources": [source]},
    )

    response = TestClient(app).post("/agent/run/stream", json={"question": "LlamaIndex use cases"})
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["step", "answer_delta", "sources", "done"]
    assert events[2]["data"]["sources"] == [source]
    message = model.invocations[1][-1]
    assert isinstance(message, ToolMessage)
    payload = json.loads(str(message.content))
    assert payload["evidence"] == [{"citation": "[1]", **source}]
    assert payload["sources"] == ["[1] llamaindex.md"]
