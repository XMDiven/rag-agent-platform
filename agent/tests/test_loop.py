import json
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

from agent_app.orchestration.executor import ToolResult
from agent_app.orchestration.loop import run_agent_loop


@dataclass(frozen=True)
class ModelInvocation:
    messages: list[Any]
    tool_names: list[str] | None
    tool_choice: str | None


class FakeBoundModel:
    def __init__(
        self,
        model: "FakeModel",
        tools: list[dict[str, Any]],
        tool_choice: str,
    ) -> None:
        self.model = model
        self.tools = tools
        self.tool_choice = tool_choice

    def invoke(self, messages: list[Any]) -> AIMessage:
        return self.model.invoke_with_binding(
            messages=messages,
            tools=self.tools,
            tool_choice=self.tool_choice,
        )


class FakeModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses
        self.invocations: list[ModelInvocation] = []

    def bind_tools(
        self,
        tools: list[dict[str, Any]],
        tool_choice: str,
    ) -> FakeBoundModel:
        return FakeBoundModel(
            model=self,
            tools=tools,
            tool_choice=tool_choice,
        )

    def invoke(self, messages: list[Any]) -> AIMessage:
        return self.invoke_with_binding(
            messages=messages,
            tools=None,
            tool_choice=None,
        )

    def invoke_with_binding(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | None,
    ) -> AIMessage:
        self.invocations.append(
            ModelInvocation(
                messages=list(messages),
                tool_names=tool_names(tools),
                tool_choice=tool_choice,
            )
        )
        return self.responses.pop(0)


def tool_names(tools: list[dict[str, Any]] | None) -> list[str] | None:
    if tools is None:
        return None

    return [str(tool["function"]["name"]) for tool in tools]


def tool_call_message(
    *,
    call_id: str = "call_1",
    name: str = "retrieval_tool",
    args: dict[str, Any] | None = None,
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": call_id,
                "name": name,
                "args": args or {},
            }
        ],
    )


def test_loop_returns_final_answer_when_model_answers_directly() -> None:
    fake_llm = FakeModel(
        responses=[
            AIMessage(content="Final answer from the model."),
        ]
    )
    llm = cast(ChatOpenAI, fake_llm)

    def fail_if_tool_runs(name: str, args: dict[str, Any]) -> ToolResult:
        raise AssertionError(f"tool should not run: {name} {args}")

    result = run_agent_loop(
        question="What is LangChain?",
        llm=llm,
        execute_tool=fail_if_tool_runs,
        max_steps=4,
    )

    assert result.answer == "Final answer from the model."
    assert result.sources == []
    assert result.tool_results == []
    assert result.termination_reason == "final_answer"
    assert result.steps == [
        {
            "round": 1,
            "status": "final_answer",
            "tool_name": None,
        }
    ]
    assert "fallback_tool" not in fake_llm.invocations[0].tool_names


def test_loop_appends_ai_message_and_tool_message_before_next_round() -> None:
    first_message = tool_call_message(
        call_id="call_retrieval_1",
        args={"question": "What is RAG?"},
    )
    fake_llm = FakeModel(
        responses=[
            first_message,
            AIMessage(content="RAG uses retrieval to ground answers."),
        ]
    )
    llm = cast(ChatOpenAI, fake_llm)
    tool_calls: list[dict[str, Any]] = []

    def execute_tool(name: str, args: dict[str, Any]) -> ToolResult:
        tool_calls.append(
            {
                "name": name,
                "args": args,
            }
        )
        return ToolResult(
            tool_name=name,
            status="success",
            output={
                "answer": "Retrieved RAG context.",
                "sources": [
                    {
                        "source": "data/raw/rag.md",
                        "section_path": "Intro",
                    }
                ],
                "trace": [
                    {
                        "step": "internal rag trace",
                    }
                ],
            },
            attempts=[
                {
                    "attempt": 1,
                    "status": "success",
                }
            ],
        )

    result = run_agent_loop(
        question="What is RAG?",
        llm=llm,
        execute_tool=execute_tool,
        max_steps=4,
    )

    assert tool_calls == [
        {
            "name": "retrieval_tool",
            "args": {"question": "What is RAG?"},
        }
    ]
    assert result.answer == "RAG uses retrieval to ground answers."
    assert result.termination_reason == "final_answer"
    assert result.tool_results[0].output["trace"] == [
        {
            "step": "internal rag trace",
        }
    ]
    assert result.steps[0] == {
        "round": 1,
        "status": "tool_executed",
        "tool_name": "retrieval_tool",
        "tool_args": {"question": "What is RAG?"},
        "tool_status": "success",
    }

    second_messages = fake_llm.invocations[1].messages
    assert second_messages[-2] is first_message
    assert isinstance(second_messages[-1], ToolMessage)
    assert second_messages[-1].tool_call_id == "call_retrieval_1"

    tool_payload = json.loads(str(second_messages[-1].content))
    assert tool_payload == {
        "status": "success",
        "answer": "Retrieved RAG context.",
        "sources": ["data/raw/rag.md"],
    }


def test_loop_finalizes_with_tool_choice_none_when_max_steps_is_reached() -> None:
    fake_llm = FakeModel(
        responses=[
            tool_call_message(call_id="call_1", args={"question": "first"}),
            tool_call_message(call_id="call_2", args={"question": "second"}),
            AIMessage(content="Forced final answer from gathered context."),
        ]
    )
    llm = cast(ChatOpenAI, fake_llm)
    tool_calls: list[dict[str, Any]] = []

    def execute_tool(name: str, args: dict[str, Any]) -> ToolResult:
        tool_calls.append(
            {
                "name": name,
                "args": args,
            }
        )
        return ToolResult(
            tool_name=name,
            status="success",
            output={
                "answer": f"context for {args['question']}",
                "sources": [],
            },
            attempts=[
                {
                    "attempt": 1,
                    "status": "success",
                }
            ],
        )

    result = run_agent_loop(
        question="Compare two tools.",
        llm=llm,
        execute_tool=execute_tool,
        max_steps=2,
    )

    assert result.answer == "Forced final answer from gathered context."
    assert result.termination_reason == "max_steps"
    assert len(tool_calls) == 2
    assert len(fake_llm.invocations) == 3
    assert fake_llm.invocations[0].tool_names is not None
    assert fake_llm.invocations[1].tool_names is not None
    assert fake_llm.invocations[2].tool_names is not None
    assert fake_llm.invocations[2].tool_choice == "none"


def test_loop_feeds_tool_error_back_to_model() -> None:
    first_message = tool_call_message(
        call_id="call_failed_1",
        args={"question": "What is RAG?"},
    )
    fake_llm = FakeModel(
        responses=[
            first_message,
            AIMessage(content="The retrieval tool failed, so I cannot answer."),
        ]
    )
    llm = cast(ChatOpenAI, fake_llm)

    def execute_tool(name: str, args: dict[str, Any]) -> ToolResult:
        raise RuntimeError("rag unavailable")

    result = run_agent_loop(
        question="What is RAG?",
        llm=llm,
        execute_tool=execute_tool,
        max_steps=4,
    )

    assert result.answer == "The retrieval tool failed, so I cannot answer."
    assert result.termination_reason == "final_answer"
    assert result.tool_results[0].tool_name == "retrieval_tool"
    assert result.tool_results[0].status == "failed"
    assert result.tool_results[0].output == {
        "error_type": "RuntimeError",
        "error": "rag unavailable",
    }
    assert result.steps[0] == {
        "round": 1,
        "status": "tool_failed",
        "tool_name": "retrieval_tool",
        "tool_args": {"question": "What is RAG?"},
        "tool_status": "failed",
    }

    second_messages = fake_llm.invocations[1].messages
    assert second_messages[-2] is first_message
    assert isinstance(second_messages[-1], ToolMessage)
    assert second_messages[-1].tool_call_id == "call_failed_1"

    tool_payload = json.loads(str(second_messages[-1].content))
    assert tool_payload == {
        "status": "failed",
        "error_type": "RuntimeError",
        "error": "rag unavailable",
    }
