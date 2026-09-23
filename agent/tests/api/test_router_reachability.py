from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
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
