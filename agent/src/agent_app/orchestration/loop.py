import json
import re
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


# 每次 retrieval_tool 调用返回的 [1]-[7] 只在那一次调用的来源列表里有效。
# 多轮检索的来源会被聚合成一个更长的列表，沿用原编号必然指向错误的证据，
# 所以回灌给模型前先剥离，并给来源标上聚合列表中的全局序号。
CITATION_MARKER_PATTERN = re.compile(r"\s*\[\d+\]")


def strip_citation_markers(text: str) -> str:
    return CITATION_MARKER_PATTERN.sub("", text)


def compact_source_labels(sources: Any, start_index: int = 1) -> list[str]:
    if not isinstance(sources, list):
        return []

    labels: list[str] = []
    next_index = start_index

    for source in sources:
        if isinstance(source, dict) and source.get("source"):
            labels.append(f"[{next_index}] {source['source']}")
            next_index += 1

    return labels


def compact_tool_payload(
    tool_result: ToolResult,
    start_index: int = 1,
) -> dict[str, Any]:
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
        payload["answer"] = strip_citation_markers(answer)

    payload["sources"] = compact_source_labels(
        output.get("sources"),
        start_index,
    )

    if tool_result.tool_name == "question_decompose_tool":
        sub_results = output.get("sub_results")
        failed_sub_questions: list[dict[str, str]] = []

        if isinstance(sub_results, list):
            for sub_result in sub_results:
                if not isinstance(sub_result, dict):
                    continue

                question = sub_result.get("question")
                if (
                    sub_result.get("status") == "failed"
                    and isinstance(question, str)
                    and question.strip()
                ):
                    failed_sub_questions.append(
                        {
                            "question": question.strip(),
                            "reason": "retrieval_failed",
                        }
                    )

        if failed_sub_questions:
            payload["failed_sub_questions"] = failed_sub_questions

    return payload


def count_tool_sources(tool_result: ToolResult) -> int:
    if not isinstance(tool_result.output, dict):
        return 0

    sources = tool_result.output.get("sources")
    if not isinstance(sources, list):
        return 0

    return sum(1 for source in sources if isinstance(source, dict))


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
    next_source_index = 1
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
                    compact_tool_payload(tool_result, next_source_index),
                    ensure_ascii=False,
                ),
                tool_call_id=str(tool_call["id"]),
            )
        )
        next_source_index += count_tool_sources(tool_result)

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
