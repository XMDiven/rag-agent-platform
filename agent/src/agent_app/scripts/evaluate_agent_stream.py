from __future__ import annotations

from collections.abc import Callable, Iterator
from time import perf_counter
from typing import Any

from agent_app.schemas.stream import AgentStreamEvent
from agent_app.scripts.evaluate_agent import AgentEvalCase
from agent_app.streaming_service import stream_agent_events


def evaluate_stream_case(
    case: AgentEvalCase,
    stream_fn: Callable[[str], Iterator[AgentStreamEvent]] = (
        stream_agent_events
    ),
    timer: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    started_at = timer()
    first_event_latency: float | None = None
    first_answer_latency: float | None = None
    total_latency = 0.0
    event_types: list[str] = []
    protocol_errors: list[str] = []
    tools: list[str] = []
    step_count = 0
    source_count = 0
    answer_character_count = 0
    done_count = 0
    phase = "steps"
    error_code: str | None = None
    termination_reason: str | None = None
    selected_tool: str | None = None
    tool_status: str | None = None
    exception_type: str | None = None

    def add_protocol_error(code: str) -> None:
        if code not in protocol_errors:
            protocol_errors.append(code)

    try:
        for event in stream_fn(case.question):
            elapsed = round(timer() - started_at, 3)
            total_latency = elapsed
            if first_event_latency is None:
                first_event_latency = elapsed

            event_types.append(event.type)

            if done_count:
                add_protocol_error("event_after_done")

            if event.type == "step":
                if phase != "steps":
                    add_protocol_error("step_out_of_order")
                step_count += 1
                tools.append(event.data.tool_name)
            elif event.type == "answer_delta":
                if phase in {"sources", "error", "done"}:
                    add_protocol_error("answer_out_of_order")
                phase = "answer"
                answer_character_count += len(event.data.text)
                if first_answer_latency is None:
                    first_answer_latency = elapsed
            elif event.type == "sources":
                if phase != "answer":
                    add_protocol_error("sources_out_of_order")
                phase = "sources"
                source_count = len(event.data.sources)
            elif event.type == "error":
                if phase in {"sources", "error", "done"}:
                    add_protocol_error("error_out_of_order")
                phase = "error"
                error_code = event.data.code
            elif event.type == "done":
                done_count += 1
                if phase not in {"sources", "error"}:
                    add_protocol_error("done_before_result")
                phase = "done"
                termination_reason = event.data.termination_reason
                selected_tool = event.data.selected_tool
                tool_status = event.data.tool_status
    except Exception as error:
        total_latency = round(timer() - started_at, 3)
        exception_type = type(error).__name__
        add_protocol_error("stream_exception")

    if done_count == 0:
        add_protocol_error("missing_done")
    elif done_count > 1:
        add_protocol_error("multiple_done")

    if done_count and (not event_types or event_types[-1] != "done"):
        add_protocol_error("done_not_final")

    completed = (
        done_count == 1
        and bool(event_types)
        and event_types[-1] == "done"
    )
    protocol_valid = not protocol_errors
    stream_succeeded = (
        protocol_valid
        and completed
        and error_code is None
        and tool_status == "success"
    )

    return {
        "id": case.id,
        "question": case.question,
        "event_types": event_types,
        "tools": tools,
        "step_count": step_count,
        "source_count": source_count,
        "answer_character_count": answer_character_count,
        "first_event_latency_seconds": first_event_latency,
        "first_answer_latency_seconds": first_answer_latency,
        "total_latency_seconds": total_latency,
        "termination_reason": termination_reason,
        "selected_tool": selected_tool,
        "tool_status": tool_status,
        "error_code": error_code,
        "exception_type": exception_type,
        "completed": completed,
        "protocol_valid": protocol_valid,
        "stream_succeeded": stream_succeeded,
        "normal_termination": termination_reason
        in {"final_answer", "single_step"},
        "mixed_model_output": error_code == "mixed_model_output",
        "protocol_errors": protocol_errors,
    }
