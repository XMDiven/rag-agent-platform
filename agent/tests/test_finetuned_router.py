"""The fine-tuned router is an optional backend that must never break the agent.

Anything it returns that this platform cannot dispatch -- a non-tool decision,
a tool outside the offered menu, an HTTP failure -- has to surface as an
exception so the planner degrades to the existing LLM path.
"""

import json

import pytest

from agent_app.orchestration import finetuned_router
from agent_app.orchestration.finetuned_router import (
    KNOWLEDGE_TOOL_NAMES,
    select_tool_with_finetuned_router,
)


def fake_response(decision: dict, latency_ms: float = 12.5) -> dict:
    return {
        "decision": decision,
        "model_version": "qwen3-1.7b-toolcall-v2",
        "adapter_revision": "8109961df2e1",
        "latency_ms": latency_ms,
    }


def patch_post(monkeypatch, handler):
    monkeypatch.setattr(finetuned_router, "_post_json", handler)


def test_a_tool_call_becomes_a_tool_selection(monkeypatch):
    patch_post(
        monkeypatch,
        lambda url, payload: fake_response(
            {
                "action": "tool_call",
                "tool_call": {
                    "name": "retrieval_tool",
                    "arguments": {"question": "什么是 RAG"},
                },
            }
        ),
    )

    selection = select_tool_with_finetuned_router("什么是 RAG")

    assert selection.tool.name == "retrieval_tool"
    assert selection.tool_args == {"question": "什么是 RAG"}
    assert "8109961df2e1" in selection.reason


def test_only_the_three_knowledge_tools_are_offered(monkeypatch):
    """The platform dispatches these three; offering more invites off-menu calls."""
    seen = {}

    def handler(url, payload):
        seen.update(payload)
        return fake_response(
            {"action": "tool_call", "tool_call": {"name": "retrieval_tool", "arguments": {"question": "q"}}}
        )

    patch_post(monkeypatch, handler)
    select_tool_with_finetuned_router("q")

    assert sorted(seen["tools"]) == sorted(KNOWLEDGE_TOOL_NAMES)
    assert seen["messages"] == [{"role": "user", "content": "q"}]


@pytest.mark.parametrize(
    "decision",
    [
        {"action": "clarify", "question": "请补充信息。"},
        {"action": "direct_answer", "answer": "你好。"},
        {"action": "handoff", "reason": "超出范围。"},
    ],
)
def test_a_non_tool_decision_is_refused(monkeypatch, decision):
    """This platform has no path for these; degrading beats improvising."""
    patch_post(monkeypatch, lambda url, payload: fake_response(decision))

    with pytest.raises(ValueError, match="not a tool call"):
        select_tool_with_finetuned_router("q")


def test_a_tool_outside_the_offered_menu_is_refused(monkeypatch):
    patch_post(
        monkeypatch,
        lambda url, payload: fake_response(
            {
                "action": "tool_call",
                "tool_call": {"name": "create_refund_request", "arguments": {}},
            }
        ),
    )

    with pytest.raises(ValueError, match="off-menu"):
        select_tool_with_finetuned_router("q")


def test_a_transport_failure_propagates(monkeypatch):
    def boom(url, payload):
        raise TimeoutError("router unreachable")

    patch_post(monkeypatch, boom)

    with pytest.raises(TimeoutError):
        select_tool_with_finetuned_router("q")


def test_a_malformed_body_is_refused(monkeypatch):
    patch_post(monkeypatch, lambda url, payload: {"unexpected": True})

    with pytest.raises((KeyError, ValueError)):
        select_tool_with_finetuned_router("q")


def test_the_request_body_is_json_serialisable(monkeypatch):
    captured = {}
    patch_post(
        monkeypatch,
        lambda url, payload: captured.update(payload)
        or fake_response(
            {"action": "tool_call", "tool_call": {"name": "summary_tool", "arguments": {"text": "t"}}}
        ),
    )
    select_tool_with_finetuned_router("总结这段：t")

    json.dumps(captured)
