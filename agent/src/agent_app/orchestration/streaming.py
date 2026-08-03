import json
from collections.abc import Callable, Iterator
from typing import Any, Literal

from langchain_core.messages import (
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_chunk_to_message,
)

from agent_app.orchestration.executor import ToolResult
from agent_app.orchestration.loop import (
    _FALLBACK_ANSWER,
    _SKIPPED_TOOL_PAYLOAD,
    build_failed_tool_result,
    build_loop_tools,
    collect_tool_sources,
    compact_tool_payload,
)
from agent_app.prompts import AGENT_LOOP_SYSTEM_PROMPT
from agent_app.schemas.stream import (
    AgentStreamEvent,
    AnswerDeltaData,
    AnswerDeltaEvent,
    DoneData,
    DoneEvent,
    SourcesData,
    SourcesEvent,
    StepData,
    StepEvent,
)

OutputMode = Literal["tool", "answer"]


class MixedModelOutputError(RuntimeError):
    pass


class EmptyModelOutputError(RuntimeError):
    pass


def chunk_text(chunk: AIMessageChunk) -> str:
    return chunk.content if isinstance(chunk.content, str) else ""


def stream_agent_loop(
    question: str,
    llm: Any,
    execute_tool: Callable[[str, dict[str, Any]], ToolResult],
    max_steps: int = 4,
) -> Iterator[AgentStreamEvent]:
    messages: list[BaseMessage] = [
        SystemMessage(content=AGENT_LOOP_SYSTEM_PROMPT),
        HumanMessage(content=question),
    ]
    tool_results: list[ToolResult] = []
    selected_tool = "fallback_tool"
    tool_calling_llm = llm.bind_tools(
        build_loop_tools(),
        tool_choice="auto",
    )

    for round_index in range(1, max_steps + 1):
        combined: AIMessageChunk | None = None
        mode: OutputMode | None = None

        for chunk in tool_calling_llm.stream(messages):
            combined = chunk if combined is None else combined + chunk
            has_tool_chunk = bool(chunk.tool_call_chunks)
            text = chunk_text(chunk)

            if has_tool_chunk:
                if mode == "answer":
                    raise MixedModelOutputError
                mode = "tool"

            if text:
                if mode == "tool":
                    raise MixedModelOutputError
                mode = "answer"
                yield AnswerDeltaEvent(data=AnswerDeltaData(text=text))

        if combined is None or mode is None:
            raise EmptyModelOutputError

        ai_message = message_chunk_to_message(combined)
        messages.append(ai_message)

        if mode == "answer":
            yield SourcesEvent(
                data=SourcesData(
                    sources=collect_tool_sources(tool_results)
                )
            )
            yield DoneEvent(
                data=DoneData(
                    termination_reason="final_answer",
                    selected_tool=selected_tool,
                    tool_status="success",
                )
            )
            return

        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls:
            raise EmptyModelOutputError

        tool_call = tool_calls[0]
        tool_name = str(tool_call["name"])
        tool_args = tool_call.get("args") or {}
        selected_tool = tool_name

        try:
            tool_result = execute_tool(tool_name, tool_args)
        except Exception as error:
            tool_result = build_failed_tool_result(tool_name, error)

        tool_results.append(tool_result)
        yield StepEvent(
            data=StepData(
                round=round_index,
                status=(
                    "tool_failed"
                    if tool_result.status == "failed"
                    else "tool_executed"
                ),
                tool_name=tool_name,
                tool_args=tool_args,
                tool_status=(
                    "failed"
                    if tool_result.status == "failed"
                    else "success"
                ),
            )
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

    emitted_text = False
    final_llm = llm.bind_tools(build_loop_tools(), tool_choice="none")

    for chunk in final_llm.stream(messages):
        if chunk.tool_call_chunks:
            raise MixedModelOutputError

        text = chunk_text(chunk)
        if text:
            emitted_text = True
            yield AnswerDeltaEvent(data=AnswerDeltaData(text=text))

    if not emitted_text:
        yield AnswerDeltaEvent(data=AnswerDeltaData(text=_FALLBACK_ANSWER))

    yield SourcesEvent(
        data=SourcesData(sources=collect_tool_sources(tool_results))
    )
    yield DoneEvent(
        data=DoneData(
            termination_reason="max_steps" if emitted_text else "failed",
            selected_tool=selected_tool,
            tool_status="success" if emitted_text else "failed",
        )
    )
