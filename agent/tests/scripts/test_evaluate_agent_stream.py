from agent_app.schemas.stream import (
    AnswerDeltaData,
    AnswerDeltaEvent,
    DoneData,
    DoneEvent,
    ErrorData,
    ErrorEvent,
    SourcesData,
    SourcesEvent,
    StepData,
    StepEvent,
)
from agent_app.scripts.evaluate_agent import AgentEvalCase
from agent_app.scripts.evaluate_agent_stream import evaluate_stream_case


def eval_case() -> AgentEvalCase:
    return AgentEvalCase(
        id="stream_case",
        question="What is RAG?",
        allowed_tools=["retrieval_tool"],
        required_tools=["retrieval_tool"],
        allowed_termination_reasons=["final_answer"],
        requires_sources=True,
        max_steps=4,
    )


def timer_values(*values: float):
    iterator = iter(values)
    return lambda: next(iterator)


def successful_events():
    yield StepEvent(
        data=StepData(
            round=1,
            status="tool_executed",
            tool_name="retrieval_tool",
            tool_args={"question": "What is RAG?"},
            tool_status="success",
        )
    )
    yield AnswerDeltaEvent(data=AnswerDeltaData(text="RAG"))
    yield AnswerDeltaEvent(data=AnswerDeltaData(text=" answer"))
    yield SourcesEvent(
        data=SourcesData(sources=[{"source": "rag.md", "snippet": "secret"}])
    )
    yield DoneEvent(
        data=DoneData(
            termination_reason="final_answer",
            selected_tool="retrieval_tool",
            tool_status="success",
        )
    )


def test_evaluate_stream_case_records_metadata_without_content() -> None:
    result = evaluate_stream_case(
        eval_case(),
        stream_fn=lambda question: successful_events(),
        timer=timer_values(10.0, 11.0, 12.0, 12.5, 13.0, 14.0),
    )

    assert result["event_types"] == [
        "step",
        "answer_delta",
        "answer_delta",
        "sources",
        "done",
    ]
    assert result["step_count"] == 1
    assert result["source_count"] == 1
    assert result["answer_character_count"] == 10
    assert result["first_event_latency_seconds"] == 1.0
    assert result["first_answer_latency_seconds"] == 2.0
    assert result["total_latency_seconds"] == 4.0
    assert result["termination_reason"] == "final_answer"
    assert result["selected_tool"] == "retrieval_tool"
    assert result["completed"] is True
    assert result["protocol_valid"] is True
    assert result["stream_succeeded"] is True
    assert "answer" not in result
    assert "sources" not in result


def test_evaluate_stream_case_counts_mixed_output_as_stable_failure() -> None:
    def mixed_events():
        yield StepEvent(
            data=StepData(
                round=1,
                status="tool_executed",
                tool_name="retrieval_tool",
                tool_args={},
                tool_status="success",
            )
        )
        yield AnswerDeltaEvent(data=AnswerDeltaData(text="Let me search"))
        yield ErrorEvent(
            data=ErrorData(
                code="mixed_model_output",
                message="safe message",
            )
        )
        yield DoneEvent(
            data=DoneData(
                termination_reason="failed",
                selected_tool="retrieval_tool",
                tool_status="failed",
            )
        )

    result = evaluate_stream_case(
        eval_case(),
        stream_fn=lambda question: mixed_events(),
        timer=timer_values(0.0, 1.0, 2.0, 3.0, 4.0),
    )

    assert result["completed"] is True
    assert result["protocol_valid"] is True
    assert result["stream_succeeded"] is False
    assert result["mixed_model_output"] is True
    assert result["error_code"] == "mixed_model_output"
    assert "safe message" not in str(result)


def test_evaluate_stream_case_flags_missing_done() -> None:
    result = evaluate_stream_case(
        eval_case(),
        stream_fn=lambda question: iter(
            [AnswerDeltaEvent(data=AnswerDeltaData(text="partial"))]
        ),
        timer=timer_values(0.0, 1.0),
    )

    assert result["completed"] is False
    assert result["protocol_valid"] is False
    assert result["protocol_errors"] == ["missing_done"]


def test_evaluate_stream_case_hides_unhandled_exception_message() -> None:
    def broken_stream():
        raise RuntimeError("api_key=secret")
        yield

    result = evaluate_stream_case(
        eval_case(),
        stream_fn=lambda question: broken_stream(),
        timer=timer_values(0.0, 1.0),
    )

    assert result["protocol_valid"] is False
    assert result["exception_type"] == "RuntimeError"
    assert "api_key" not in str(result)
    assert "secret" not in str(result)
