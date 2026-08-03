import logging
from collections.abc import Callable, Iterator
from typing import Any

from rag_app.infrastructure.llm_client import get_client
from rag_app.retrieval.query_analyzer import analyze_query

from agent_app.orchestration.executor import run_tool
from agent_app.orchestration.streaming import (
    MixedModelOutputError,
    stream_agent_loop,
)
from agent_app.schemas.stream import (
    AgentStreamEvent,
    AnswerDeltaData,
    AnswerDeltaEvent,
    DoneData,
    DoneEvent,
    ErrorData,
    ErrorEvent,
    SourcesData,
    SourcesEvent,
    encode_event,
)
from agent_app.service import (
    AGENT_MAX_STEPS,
    AgentRunResult,
    run_agent_once,
    should_skip_loop,
)

logger = logging.getLogger(__name__)


def _result_output(result: AgentRunResult) -> dict[str, Any]:
    output = result.tool_result.output
    return output if isinstance(output, dict) else {}


def _result_answer(output: dict[str, Any]) -> str:
    for key in ("answer", "summary", "error"):
        value = output.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _failed_done_event() -> DoneEvent:
    return DoneEvent(
        data=DoneData(
            termination_reason="failed",
            selected_tool="fallback_tool",
            tool_status="failed",
        )
    )


def _failure_events(
    code: str = "agent_stream_failed",
    message: str = "Agent 流式执行失败，请重试",
) -> Iterator[AgentStreamEvent]:
    yield ErrorEvent(data=ErrorData(code=code, message=message))
    yield _failed_done_event()


def _events_from_single_result(
    result: AgentRunResult,
) -> Iterator[AgentStreamEvent]:
    if result.tool_result.status == "failed":
        yield from _failure_events()
        return

    output = _result_output(result)
    answer = _result_answer(output)
    sources = output.get("sources")

    if not answer:
        yield from _failure_events(
            code="empty_model_output",
            message="Agent 未生成有效回答，请重试",
        )
        return

    yield AnswerDeltaEvent(data=AnswerDeltaData(text=answer))
    yield SourcesEvent(
        data=SourcesData(
            sources=(
                [item for item in sources if isinstance(item, dict)]
                if isinstance(sources, list)
                else []
            )
        )
    )
    yield DoneEvent(
        data=DoneData(
            termination_reason="single_step",
            selected_tool=result.plan.tool.name,
            tool_status=(
                "failed"
                if result.tool_result.status == "failed"
                else "success"
            ),
        )
    )


def stream_agent_events(
    question: str,
    analyze_fn: Callable[..., Any] = analyze_query,
    run_once_fn: Callable[..., AgentRunResult] = run_agent_once,
    stream_loop_fn: Callable[..., Iterator[AgentStreamEvent]] = (
        stream_agent_loop
    ),
    get_llm_fn: Callable[[], Any] = get_client,
    execute_tool_fn: Callable[..., Any] = run_tool,
) -> Iterator[AgentStreamEvent]:
    try:
        analysis = analyze_fn(question)
    except Exception as error:
        logger.warning(
            "agent.stream analysis failed error_type=%s",
            type(error).__name__,
        )
        yield from _failure_events()
        return

    if should_skip_loop(analysis):
        try:
            yield from _events_from_single_result(
                run_once_fn(question, analysis)
            )
        except Exception as error:
            logger.warning(
                "agent.stream fallback failed error_type=%s",
                type(error).__name__,
            )
            yield from _failure_events()
        return

    emitted = False
    try:
        for event in stream_loop_fn(
            question=question,
            llm=get_llm_fn(),
            execute_tool=execute_tool_fn,
            max_steps=AGENT_MAX_STEPS,
        ):
            emitted = True
            yield event
    except MixedModelOutputError:
        yield from _failure_events(
            code="mixed_model_output",
            message="模型返回了不兼容的混合流，请重试",
        )
    except Exception as error:
        logger.warning(
            "agent.stream failed error_type=%s",
            type(error).__name__,
        )
        if emitted:
            yield from _failure_events()
            return

        try:
            yield from _events_from_single_result(
                run_once_fn(question, analysis)
            )
        except Exception as fallback_error:
            logger.warning(
                "agent.stream fallback failed error_type=%s",
                type(fallback_error).__name__,
            )
            yield from _failure_events()


def stream_agent_ndjson(question: str) -> Iterator[str]:
    for event in stream_agent_events(question):
        yield encode_event(event)
