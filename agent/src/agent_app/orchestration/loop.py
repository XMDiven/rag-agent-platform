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

from agent_app.orchestration.executor import ToolResult, tool_model_calls
from rag_app.infrastructure.request_budget import BudgetExceeded, check_request_budget, get_request_budget
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


def requires_final_answer() -> bool:
    budget = get_request_budget()
    return budget is not None and not budget.can_start_tool(1)


def skip_tool_for_budget(messages, tool_calls) -> bool:
    budget = get_request_budget()
    if budget is None:
        return False
    first = tool_calls[0]
    if budget.can_start_tool(tool_model_calls(str(first['name']), first.get('args') or {})):
        return False
    # Native tool calling requires an acknowledgment for every returned ID.
    for call in tool_calls:
        messages.append(ToolMessage(
            content=json.dumps({'status': 'skipped', 'reason': 'reserved capacity for final answer'}),
            tool_call_id=str(call['id']),
        ))
    return True


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
        if isinstance(source, dict):
            labels.append(f"[{next_index}] {source.get('source') or 'unknown'}")
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

    summary = output.get("summary")
    if isinstance(summary, str):
        payload["summary"] = summary

    payload["sources"] = compact_source_labels(
        output.get("sources"),
        start_index,
    )

    sources = output.get("sources")
    source_entries = (
        [source for source in sources if isinstance(source, dict)]
        if isinstance(sources, list)
        else []
    )
    evidence = [
        {
            "citation": f"[{index}]",
            "source": source.get("source") or "unknown",
            "section_path": source.get("section_path") or "",
            "snippet": source["snippet"],
        }
        for index, source in enumerate(source_entries, start=start_index)
        if isinstance(source.get("snippet"), str) and source["snippet"].strip()
    ]
    if evidence:
        payload["evidence"] = evidence

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

    budget_final = False
    final_round = max_steps + 1
    for round_index in range(1, max_steps + 1):
        check_request_budget()
        if requires_final_answer():
            budget_final = True
            final_round = round_index
            break
        ai_message: AIMessage = tool_calling_llm.invoke(messages)
        check_request_budget()
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

        if skip_tool_for_budget(messages, tool_calls):
            budget_final = True
            final_round = round_index + 1
            break

        tool_call = tool_calls[0]
        tool_name = str(tool_call["name"])
        tool_args = tool_call.get("args") or {}

        try:
            tool_result = execute_tool(tool_name, tool_args)
            check_request_budget()
        except BudgetExceeded:
            raise
        except Exception as error:
            check_request_budget()
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
        check_request_budget()
        final_message = llm.bind_tools(build_loop_tools(), tool_choice="none").invoke(messages)
        check_request_budget()
        final_answer = extract_message_text(final_message)
    except BudgetExceeded:
        raise
    except Exception:
        check_request_budget()
        final_answer = ""

    if final_answer:
        termination_reason: TerminationReason = "final_answer" if budget_final else "max_steps"
    else:
        termination_reason = "failed"
        final_answer = _FALLBACK_ANSWER

    steps.append(
        {
            "round": final_round,
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
