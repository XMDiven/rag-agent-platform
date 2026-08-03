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
import json
from datetime import datetime, timezone

import pytest

from agent_app.scripts.evaluate_agent_stream import (
    evaluate_stream_case,
    main,
    run_evaluation,
    summarize_results,
    write_report,
)


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


def stream_result(**overrides):
    result = {
        "id": "stream_case",
        "completed": True,
        "protocol_valid": True,
        "stream_succeeded": True,
        "normal_termination": True,
        "mixed_model_output": False,
        "error_code": None,
        "first_event_latency_seconds": 1.0,
        "first_answer_latency_seconds": 2.0,
        "total_latency_seconds": 4.0,
    }
    result.update(overrides)
    return result


def test_summarize_results_calculates_stability_and_latency() -> None:
    results = [
        stream_result(),
        stream_result(
            id="mixed",
            stream_succeeded=False,
            normal_termination=False,
            mixed_model_output=True,
            error_code="mixed_model_output",
            first_event_latency_seconds=2.0,
            first_answer_latency_seconds=3.0,
            total_latency_seconds=5.0,
        ),
        stream_result(
            id="invalid",
            completed=False,
            protocol_valid=False,
            stream_succeeded=False,
            normal_termination=False,
            first_event_latency_seconds=3.0,
            first_answer_latency_seconds=None,
            total_latency_seconds=6.0,
        ),
    ]

    assert summarize_results(results) == {
        "total": 3,
        "completed_count": 2,
        "completion_rate": 0.667,
        "protocol_valid_count": 2,
        "protocol_valid_rate": 0.667,
        "stream_succeeded_count": 1,
        "stream_success_rate": 0.333,
        "normal_termination_count": 1,
        "normal_termination_rate": 0.333,
        "mixed_model_output_count": 1,
        "mixed_model_output_rate": 0.333,
        "protocol_invalid_count": 1,
        "error_code_counts": {"mixed_model_output": 1},
        "average_first_event_latency_seconds": 2.0,
        "p95_first_event_latency_seconds": 3.0,
        "average_first_answer_latency_seconds": 2.5,
        "p95_first_answer_latency_seconds": 3.0,
        "average_total_latency_seconds": 5.0,
        "p95_total_latency_seconds": 6.0,
    }


def test_summarize_results_returns_zero_metrics_for_empty_results() -> None:
    summary = summarize_results([])

    assert summary["total"] == 0
    assert summary["completion_rate"] == 0.0
    assert summary["mixed_model_output_rate"] == 0.0
    assert summary["average_first_answer_latency_seconds"] == 0.0
    assert summary["error_code_counts"] == {}


def test_run_evaluation_runs_every_case_and_prints_progress(capsys) -> None:
    cases = [
        eval_case(),
        AgentEvalCase(
            id="second_case",
            question="What is Qdrant?",
            allowed_tools=["retrieval_tool"],
            required_tools=["retrieval_tool"],
            allowed_termination_reasons=["final_answer"],
            requires_sources=True,
            max_steps=4,
        ),
    ]
    seen: list[str] = []

    def fake_evaluate(case):
        seen.append(case.id)
        return stream_result(id=case.id)

    report = run_evaluation(
        cases,
        evaluate_case_fn=fake_evaluate,
        now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )
    output = capsys.readouterr().out

    assert seen == ["stream_case", "second_case"]
    assert "Evaluating Agent stream case 1/2: stream_case" in output
    assert "completed stream_case success=True" in output
    assert report["run_id"] == "20260803-120000"
    assert report["generated_at"] == "2026-08-03T12:00:00Z"
    assert report["summary"]["stream_succeeded_count"] == 2


def test_write_report_creates_directory_and_round_trips_json(tmp_path) -> None:
    report = {
        "run_id": "20260803-120000",
        "summary": {"protocol_invalid_count": 0},
        "cases": [],
    }

    path = write_report(report, output_dir=tmp_path / "nested" / "streaming")

    assert path.name == "20260803-120000.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report


@pytest.mark.parametrize(
    ("protocol_invalid_count", "exit_code"),
    [(0, 0), (1, 1)],
)
def test_main_exit_code_only_reflects_protocol_failures(
    tmp_path,
    monkeypatch,
    protocol_invalid_count: int,
    exit_code: int,
) -> None:
    report = {
        "run_id": "20260803-120000",
        "summary": {
            "total": 1,
            "stream_succeeded_count": 0,
            "mixed_model_output_count": 1,
            "protocol_invalid_count": protocol_invalid_count,
        },
        "cases": [],
    }
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_stream.load_cases",
        lambda path: [eval_case()],
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_stream.run_evaluation",
        lambda cases: report,
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_stream.write_report",
        lambda result, output_dir: tmp_path / "report.json",
    )

    assert main(["--cases", "cases.json", "--output-dir", str(tmp_path)]) == exit_code
