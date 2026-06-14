from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent_app.app.main import app

client = TestClient(app)


def test_run_agent_endpoint_uses_fallback_tool_for_empty_question() -> None:
    response = client.post(
        "/agent/run",
        json={
            "question": "",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert "plan" not in data
    assert "tool_result" not in data
    assert data["answer"] == "No retrieval is needed for this question."
    assert data["sources"] == []
    assert data["selected_tool"] == "fallback_tool"
    assert data["tool_status"] == "success"
    assert data["tool_output"] == {
        "answer": "No retrieval is needed for this question.",
        "sources": [],
    }
    assert data["trace"] == [
        {
            "step": "analyze_question",
            "status": "completed",
            "detail": {
                "needs_retrieval": False,
                "question_type": "empty",
                "reason": "empty question",
            },
        },
        {
            "step": "plan_tool",
            "status": "completed",
            "detail": {
                "tool_name": "fallback_tool",
                "reason": "question does not require retrieval",
            },
        },
        {
            "step": "execute_tool",
            "status": "success",
            "detail": {
                "tool_name": "fallback_tool",
                "attempts": [
                    {
                        "attempt": 1,
                        "status": "success",
                    }
                ],
            },
        },
    ]


def test_run_agent_endpoint_returns_success_through_loop(
    patch_loop_llm,
    make_tool_call,
    monkeypatch,
) -> None:
    patch_loop_llm(
        [
            make_tool_call("retrieval_tool", {"question": "What is RAG?"}),
            AIMessage(content="RAG answer"),
        ]
    )
    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        lambda question: {
            "answer": "ctx",
            "sources": [{"source": "rag.md"}],
            "trace": [],
        },
    )

    response = client.post(
        "/agent/run",
        json={
            "question": "What is RAG?",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert "plan" not in data
    assert "tool_result" not in data
    assert data["answer"] == "RAG answer"
    assert data["sources"] == [{"source": "rag.md"}]
    assert data["selected_tool"] == "retrieval_tool"
    assert data["tool_status"] == "success"
    assert data["tool_output"] == {
        "answer": "RAG answer",
        "sources": [{"source": "rag.md"}],
    }
    assert data["trace"][0]["step"] == "analyze_question"
    assert data["trace"][1]["step"] == "agent_loop"
    assert data["trace"][1]["detail"]["termination_reason"] == "final_answer"


def test_run_agent_endpoint_recovers_from_tool_failure_through_loop(
    patch_loop_llm,
    make_tool_call,
    monkeypatch,
) -> None:
    patch_loop_llm(
        [
            make_tool_call("retrieval_tool", {"question": "What is RAG?"}),
            AIMessage(content="I could not retrieve context for this question."),
        ]
    )

    def raise_error(question: str) -> None:
        raise RuntimeError("rag unavailable")

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        raise_error,
    )

    response = client.post(
        "/agent/run",
        json={
            "question": "What is RAG?",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["answer"] == "I could not retrieve context for this question."
    assert data["selected_tool"] == "retrieval_tool"
    assert data["tool_status"] == "success"
    assert data["trace"][1]["step"] == "agent_loop"
    assert (
        data["trace"][1]["detail"]["steps"][0]["status"] == "tool_failed"
    )
