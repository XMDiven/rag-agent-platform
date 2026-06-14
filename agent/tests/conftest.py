from typing import Any, Sequence

import pytest
from langchain_core.messages import AIMessage


class FakeChatModel:
    """Offline stand-in for ChatOpenAI used to drive the agent loop in tests.

    It replays a fixed list of AIMessage responses, one per ``invoke`` call,
    and ignores tool bindings so the loop can run without a live LLM.
    """

    def __init__(self, responses: Sequence[AIMessage]) -> None:
        self._responses = list(responses)

    def bind_tools(
        self,
        tools: Any,
        tool_choice: str = "auto",
    ) -> "FakeChatModel":
        return self

    def invoke(self, messages: Any) -> AIMessage:
        return self._responses.pop(0)


@pytest.fixture
def make_tool_call():
    def _make(
        name: str,
        args: dict[str, Any],
        call_id: str = "call_1",
    ) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[{"id": call_id, "name": name, "args": args}],
        )

    return _make


@pytest.fixture
def patch_loop_llm(monkeypatch):
    """Replace the agent loop's LLM with a scripted FakeChatModel."""

    def _install(responses: Sequence[AIMessage]) -> FakeChatModel:
        model = FakeChatModel(responses)
        monkeypatch.setattr("agent_app.service.get_client", lambda: model)
        return model

    return _install
