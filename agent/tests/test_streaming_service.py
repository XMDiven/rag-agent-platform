from types import SimpleNamespace

from agent_app.schemas.stream import (
    StepData,
    StepEvent,
)
from agent_app.streaming_service import stream_agent_events


def single_step_result(
    answer: str = "No retrieval is needed for this question.",
    output: dict | None = None,
    status: str = "success",
):
    return SimpleNamespace(
        plan=SimpleNamespace(tool=SimpleNamespace(name="fallback_tool")),
        tool_result=SimpleNamespace(
            status=status,
            output=output or {"answer": answer, "sources": []},
        ),
    )


def empty_analysis():
    return SimpleNamespace(question_type="empty")


def retrieval_analysis():
    return SimpleNamespace(question_type="knowledge")


def test_stream_agent_events_serializes_single_step_fallback() -> None:
    loop_called = False

    def fail_if_looped(**kwargs):
        nonlocal loop_called
        loop_called = True
        raise AssertionError("empty question must skip loop")

    events = list(
        stream_agent_events(
            "",
            analyze_fn=lambda question: empty_analysis(),
            run_once_fn=lambda question, analysis: single_step_result(),
            stream_loop_fn=fail_if_looped,
            get_llm_fn=lambda: object(),
            execute_tool_fn=lambda name, args: None,
        )
    )

    assert loop_called is False
    assert [event.type for event in events] == [
        "answer_delta",
        "sources",
        "done",
    ]
    assert events[-1].data.termination_reason == "single_step"
    assert events[-1].data.selected_tool == "fallback_tool"


def test_stream_agent_events_hides_error_after_partial_progress() -> None:
    def fail_after_step(**kwargs):
        yield StepEvent(
            data=StepData(
                round=1,
                status="tool_executed",
                tool_name="retrieval_tool",
                tool_args={"question": "RAG?"},
                tool_status="success",
            )
        )
        raise RuntimeError("api_key=secret")

    events = list(
        stream_agent_events(
            "RAG?",
            analyze_fn=lambda question: retrieval_analysis(),
            run_once_fn=lambda question, analysis: single_step_result(),
            stream_loop_fn=fail_after_step,
            get_llm_fn=lambda: object(),
            execute_tool_fn=lambda name, args: None,
        )
    )
    serialized = "\n".join(event.model_dump_json() for event in events)

    assert [event.type for event in events] == ["step", "error", "done"]
    assert events[1].data.code == "agent_stream_failed"
    assert events[-1].data.termination_reason == "failed"
    assert "api_key" not in serialized
    assert "secret" not in serialized


def test_stream_agent_events_falls_back_before_first_event() -> None:
    def fail_before_event(**kwargs):
        raise RuntimeError("upstream unavailable")
        yield

    events = list(
        stream_agent_events(
            "RAG?",
            analyze_fn=lambda question: retrieval_analysis(),
            run_once_fn=lambda question, analysis: single_step_result(
                "Fallback answer"
            ),
            stream_loop_fn=fail_before_event,
            get_llm_fn=lambda: object(),
            execute_tool_fn=lambda name, args: None,
        )
    )

    assert [event.type for event in events] == [
        "answer_delta",
        "sources",
        "done",
    ]
    assert events[0].data.text == "Fallback answer"


def test_stream_agent_events_preserves_summary_fallback_output() -> None:
    def fail_before_event(**kwargs):
        raise RuntimeError("upstream unavailable")
        yield

    events = list(
        stream_agent_events(
            "Summarize this text",
            analyze_fn=lambda question: retrieval_analysis(),
            run_once_fn=lambda question, analysis: single_step_result(
                output={"summary": "Short summary"}
            ),
            stream_loop_fn=fail_before_event,
            get_llm_fn=lambda: object(),
            execute_tool_fn=lambda name, args: None,
        )
    )

    assert events[0].type == "answer_delta"
    assert events[0].data.text == "Short summary"


def test_stream_agent_events_hides_fallback_failure() -> None:
    def fail_before_event(**kwargs):
        raise RuntimeError("loop failed")
        yield

    def fail_fallback(question, analysis):
        raise RuntimeError("api_key=secret")

    events = list(
        stream_agent_events(
            "RAG?",
            analyze_fn=lambda question: retrieval_analysis(),
            run_once_fn=fail_fallback,
            stream_loop_fn=fail_before_event,
            get_llm_fn=lambda: object(),
            execute_tool_fn=lambda name, args: None,
        )
    )
    serialized = "\n".join(event.model_dump_json() for event in events)

    assert [event.type for event in events] == ["error", "done"]
    assert "api_key" not in serialized
    assert "secret" not in serialized


def test_stream_agent_events_hides_analysis_failure() -> None:
    def fail_analysis(question):
        raise RuntimeError("api_key=secret")

    events = list(
        stream_agent_events(
            "RAG?",
            analyze_fn=fail_analysis,
            run_once_fn=lambda question, analysis: single_step_result(),
            get_llm_fn=lambda: object(),
            execute_tool_fn=lambda name, args: None,
        )
    )
    serialized = "\n".join(event.model_dump_json() for event in events)

    assert [event.type for event in events] == ["error", "done"]
    assert "api_key" not in serialized
    assert "secret" not in serialized


def test_stream_agent_events_hides_failed_single_step_output() -> None:
    events = list(
        stream_agent_events(
            "",
            analyze_fn=lambda question: empty_analysis(),
            run_once_fn=lambda question, analysis: single_step_result(
                output={"error": "api_key=secret"},
                status="failed",
            ),
            get_llm_fn=lambda: object(),
            execute_tool_fn=lambda name, args: None,
        )
    )
    serialized = "\n".join(event.model_dump_json() for event in events)

    assert [event.type for event in events] == ["error", "done"]
    assert "api_key" not in serialized
    assert "secret" not in serialized
