import json
from dataclasses import dataclass
from typing import Any, Callable, Literal

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI

from agent_app.orchestration.executor import ToolResult
from agent_app.prompts import AGENT_LOOP_SYSTEM_PROMPT
from agent_app.tools import list_tools

TerminationReason = Literal["final_answer", "max_steps", "failed"]


_FALLBACK_ANSWER = (
    "I could not produce a final answer within the step budget. "
    "Please retry or rephrase the question."
)

_SKIPPED_TOOL_PAYLOAD = {
    "status": "skipped",
    "reason": "only the first tool call per round is executed",
}


@dataclass(frozen=True)
class LoopResult:
    answer: str
    sources: list[dict[str, Any]]
    tool_results: list[ToolResult]
    steps: list[dict[str, Any]]
    termination_reason: TerminationReason


def build_loop_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in list_tools()
        if tool.name != "fallback_tool"
    ]


def extract_message_text(ai_message: AIMessage) -> str:
    content: str = getattr(ai_message, "content", "")

    if isinstance(content, str):
        return content

    return str(content)


def collect_tool_sources(
    tool_results: list[ToolResult],
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []

    for tool_result in tool_results:
        if not isinstance(tool_result.output, dict):
            continue

        sources = tool_result.output.get("sources", [])
        if not isinstance(sources, list):
            continue

        collected.extend(
            source for source in sources if isinstance(source, dict)
        )

    return collected


def build_failed_tool_result(
    tool_name: str,
    error: Exception,
) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        status="failed",
        output={
            "error_type": type(error).__name__,
            "error": str(error),
        },
        attempts=[
            {
                "attempt": 1,
                "status": "failed",
                "error_type": type(error).__name__,
                "error": str(error),
            }
        ],
    )


def compact_source_labels(sources: Any) -> list[str]:
    if not isinstance(sources, list):
        return []

    labels: list[str] = []

    for source in sources:
        if isinstance(source, dict) and source.get("source"):
            labels.append(str(source["source"]))

    return labels


def compact_tool_payload(tool_result: ToolResult) -> dict[str, Any]:
    output = tool_result.output if isinstance(tool_result.output, dict) else {}

    if tool_result.status == "failed":
        payload: dict[str, Any] = {
            "status": "failed",
        }

        error_type = output.get("error_type")
        if isinstance(error_type, str):
            payload["error_type"] = error_type

        error = output.get("error")
        if isinstance(error, str):
            payload["error"] = error

        return payload

    payload: dict[str, Any] = {
        "status": tool_result.status,
    }

    answer = output.get("answer")
    if isinstance(answer, str):
        payload["answer"] = answer

    payload["sources"] = compact_source_labels(output.get("sources"))

    return payload


def run_agent_loop(
    question: str,
    llm: ChatOpenAI,
    execute_tool: Callable[[str, dict[str, Any]], ToolResult],
    max_steps: int = 4,
) -> LoopResult:
    messages: list[BaseMessage] = [
        SystemMessage(content=AGENT_LOOP_SYSTEM_PROMPT),
        HumanMessage(content=question),
    ]

    tool_results: list[ToolResult] = []
    steps: list[dict[str, Any]] = []

    tool_calling_llm = llm.bind_tools(
        build_loop_tools(),
        tool_choice="auto",
    )

    for round_index in range(1, max_steps + 1):
        ai_message: AIMessage = tool_calling_llm.invoke(messages)
        messages.append(ai_message)

        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls:
            steps.append(
                {
                    "round": round_index,
                    "status": "final_answer",
                    "tool_name": None,
                }
            )

            return LoopResult(
                answer=extract_message_text(ai_message),
                sources=collect_tool_sources(tool_results),
                tool_results=tool_results,
                steps=steps,
                termination_reason="final_answer",
            )

        tool_call = tool_calls[0]
        tool_name = str(tool_call["name"])
        tool_args = tool_call.get("args") or {}

        try:
            tool_result = execute_tool(tool_name, tool_args)
        except Exception as error:
            tool_result = build_failed_tool_result(
                tool_name=tool_name,
                error=error,
            )

        tool_results.append(tool_result)

        steps.append(
            {
                "round": round_index,
                "status": (
                    "tool_failed"
                    if tool_result.status == "failed"
                    else "tool_executed"
                ),
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_status": tool_result.status,
            }
        )

        messages.append(
            ToolMessage(
                content=json.dumps(
                    compact_tool_payload(tool_result),
                    ensure_ascii=False,
                ),
                tool_call_id=str(tool_call["id"]),
            )
        )

        for skipped_call in tool_calls[1:]:
            messages.append(
                ToolMessage(
                    content=json.dumps(
                        _SKIPPED_TOOL_PAYLOAD,
                        ensure_ascii=False,
                    ),
                    tool_call_id=str(skipped_call["id"]),
                )
            )

    try:
        final_message = llm.bind_tools(build_loop_tools(), tool_choice="none").invoke(messages)
        final_answer = extract_message_text(final_message)
    except Exception:
        final_answer = ""

    if final_answer:
        termination_reason: TerminationReason = "max_steps"
    else:
        termination_reason = "failed"
        final_answer = _FALLBACK_ANSWER

    steps.append(
        {
            "round": max_steps + 1,
            "status": "forced_final_answer",
            "tool_name": None,
        }
    )

    return LoopResult(
        answer=final_answer,
        sources=collect_tool_sources(tool_results),
        tool_results=tool_results,
        steps=steps,
        termination_reason=termination_reason,
    )
