import asyncio
import json
import socket
from contextlib import closing
from datetime import datetime, timezone
from threading import Event, Thread
from time import monotonic
from unittest.mock import Mock

import httpx
import pytest
import uvicorn
from langchain_core.messages import AIMessageChunk

from agent_app.app.main import app
from agent_app.scripts import evaluate_agent_stream as stream_eval
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
        task_type="retrieval",
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


def test_evaluate_stream_case_flags_out_of_order_events() -> None:
    events = iter(
        [
            SourcesEvent(data=SourcesData(sources=[])),
            DoneEvent(
                data=DoneData(
                    termination_reason="final_answer",
                    selected_tool="retrieval_tool",
                    tool_status="success",
                )
            ),
            AnswerDeltaEvent(data=AnswerDeltaData(text="late")),
        ]
    )

    result = evaluate_stream_case(
        eval_case(),
        stream_fn=lambda question: events,
        timer=timer_values(0.0, 1.0, 2.0, 3.0),
    )

    assert result["completed"] is False
    assert result["protocol_valid"] is False
    assert result["protocol_errors"] == [
        "sources_out_of_order",
        "event_after_done",
        "answer_out_of_order",
        "done_not_final",
    ]


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
            task_type="retrieval",
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
        lambda cases, **kwargs: report,
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_stream.write_report",
        lambda result, output_dir: tmp_path / "report.json",
    )

    assert main(["--cases", "cases.json", "--output-dir", str(tmp_path)]) == exit_code


@pytest.fixture
def install_http_stream(monkeypatch):
    client_type = httpx.Client

    def install(chunks, *, error=None, status_code=200, content_type="application/x-ndjson"):
        requests = []

        class Body(httpx.SyncByteStream):
            closed = False

            def __iter__(self):
                yield from chunks
                if error is not None:
                    raise error

            def close(self):
                self.closed = True

        body = Body()

        def handle(request):
            requests.append(request)
            return httpx.Response(
                status_code,
                headers={"content-type": content_type},
                stream=body,
            )

        factory = Mock(side_effect=lambda **kwargs: client_type(
            transport=httpx.MockTransport(handle), **kwargs
        ))
        monkeypatch.setattr(stream_eval.httpx, "Client", factory)
        return requests, body, factory

    return install


def ndjson_bytes(events):
    return "".join(event.model_dump_json() + "\n" for event in events).encode("utf-8")


def test_http_stream_posts_question_and_decodes_split_utf8(install_http_stream):
    events = [
        AnswerDeltaEvent(data=AnswerDeltaData(text="中文回答")),
        SourcesEvent(data=SourcesData(sources=[])),
        DoneEvent(data=DoneData(
            termination_reason="final_answer",
            selected_tool="fallback_tool",
            tool_status="success",
        )),
    ]
    raw = ndjson_bytes(events).rstrip(b"\n")
    split = raw.index("中".encode("utf-8")) + 1
    requests, body, factory = install_http_stream([
        raw[:split], raw[split:split + 1], raw[split + 1:]
    ])

    received = list(stream_eval.stream_agent_http(
        "中文问题", url="http://agent.test/agent/run/stream", timeout=17.0
    ))

    assert received == events
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://agent.test/agent/run/stream"
    assert json.loads(requests[0].content) == {"question": "中文问题"}
    factory.assert_called_once_with(timeout=17.0)
    assert body.closed


def test_evaluate_stream_case_uses_http_by_default(install_http_stream):
    assert evaluate_stream_case.__defaults__[0] is stream_eval.stream_agent_http
    requests, body, _ = install_http_stream([ndjson_bytes(successful_events())])

    result = evaluate_stream_case(eval_case())

    assert result["stream_succeeded"] is True
    assert result["source_count"] == 1
    assert len(requests) == 1
    assert requests[0].url.path == "/agent/run/stream"
    assert body.closed
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("recovered", [True, False])
def test_http_evaluation_checks_tool_and_run_failures(install_http_stream, recovered):
    events = [StepEvent(data=StepData(
        round=1,
        status="tool_failed",
        tool_name="retrieval_tool",
        tool_status="failed",
    ))]
    if recovered:
        events.extend([
            AnswerDeltaEvent(data=AnswerDeltaData(text="Recovered answer")),
            SourcesEvent(data=SourcesData(sources=[])),
        ])
    else:
        events.append(ErrorEvent(data=ErrorData(
            code="agent_stream_failed", message="safe failure"
        )))
    events.append(DoneEvent(data=DoneData(
        termination_reason="final_answer" if recovered else "failed",
        selected_tool="retrieval_tool",
        tool_status="success" if recovered else "failed",
    )))
    _, body, _ = install_http_stream([ndjson_bytes(events)])

    result = evaluate_stream_case(
        eval_case(), stream_fn=stream_eval.stream_agent_http
    )

    assert result["completed"] is True
    assert result["protocol_valid"] is True
    assert result["stream_succeeded"] is recovered
    assert result["error_code"] == (None if recovered else "agent_stream_failed")
    assert body.closed


@pytest.mark.parametrize("disconnect", [
    None,
    httpx.ReadError("private connection error"),
    httpx.ReadTimeout("private connection error"),
])
def test_http_evaluation_records_disconnect_and_closes_response(
    install_http_stream, disconnect
):
    partial = [AnswerDeltaEvent(data=AnswerDeltaData(text="partial"))]
    _, body, _ = install_http_stream([ndjson_bytes(partial)], error=disconnect)

    result = evaluate_stream_case(
        eval_case(), stream_fn=stream_eval.stream_agent_http
    )

    assert result["completed"] is False
    assert result["stream_succeeded"] is False
    assert result["protocol_valid"] is False
    assert result["answer_character_count"] == len("partial")
    assert "missing_done" in result["protocol_errors"]
    assert result["exception_type"] == (type(disconnect).__name__ if disconnect else None)
    assert ("stream_exception" in result["protocol_errors"]) is bool(disconnect)
    assert "private connection error" not in json.dumps(result)
    assert body.closed


@pytest.mark.parametrize(
    ("chunks", "status_code", "content_type", "exception_type"),
    [
        ([b'{"private":"invalid event"}\n'], 200, "application/x-ndjson", "ValidationError"),
        ([b'{"version":2,"type":"answer_delta","data":{"text":"secret"}}\n'], 200, "application/x-ndjson", "ValidationError"),
        ([b'{"type":"answer_delta","data":{"text":"secret"}}\n'], 200, "application/x-ndjson", "ValueError"),
        ([b'{"version":1,"data":{"text":"secret"}}\n'], 200, "application/x-ndjson", "ValueError"),
        ([b'not json\n'], 200, "application/x-ndjson", "ValidationError"),
        ([b'private server error'], 503, "application/x-ndjson", "HTTPStatusError"),
        ([b'<html>private error</html>'], 200, "text/html", "ValueError"),
    ],
)
def test_http_evaluation_rejects_invalid_responses(
    install_http_stream, chunks, status_code, content_type, exception_type
):
    _, body, _ = install_http_stream(
        chunks, status_code=status_code, content_type=content_type
    )

    result = evaluate_stream_case(
        eval_case(), stream_fn=stream_eval.stream_agent_http
    )

    assert result["event_types"] == []
    assert result["protocol_valid"] is False
    assert result["protocol_errors"] == ["stream_exception", "missing_done"]
    assert result["exception_type"] == exception_type
    assert "private" not in json.dumps(result)
    assert "secret" not in json.dumps(result)
    assert body.closed


def test_main_passes_http_options_to_evaluator(tmp_path, monkeypatch, install_http_stream):
    requests, body, factory = install_http_stream([ndjson_bytes(successful_events())])
    monkeypatch.setattr(stream_eval, "load_cases", lambda path: [eval_case()])

    exit_code = main([
        "--url", "http://custom.test/agent/run/stream",
        "--timeout", "23", "--output-dir", str(tmp_path),
    ])

    assert exit_code == 0
    assert str(requests[0].url) == "http://custom.test/agent/run/stream"
    factory.assert_called_once_with(timeout=23.0)
    assert body.closed
    report = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert report["transport"] == "http"
    assert report["summary"]["stream_succeeded_count"] == 1


def test_http_evaluator_receives_first_event_before_server_finishes(monkeypatch):
    release = Event()
    model_closed = Event()

    class Model:
        def bind_tools(self, *args, **kwargs):
            return self

        async def astream(self, messages):
            try:
                yield AIMessageChunk(content="first")
                if not await asyncio.to_thread(release.wait, 3):
                    raise TimeoutError("client did not receive first event")
                yield AIMessageChunk(content=" second")
            finally:
                model_closed.set()

    monkeypatch.setattr("agent_app.streaming_service.get_client", Model)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            app, log_level="critical", lifespan="off", timeout_graceful_shutdown=1
        ))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 3
            while not server.started and thread.is_alive() and monotonic() < deadline:
                Event().wait(0.01)
            assert server.started

            def observed_stream(question):
                with closing(stream_eval.stream_agent_http(
                    question,
                    url=f"http://127.0.0.1:{port}/agent/run/stream",
                    timeout=2,
                )) as events:
                    first = next(events)
                    assert first.type == "answer_delta"
                    assert first.data.text == "first"
                    assert not model_closed.is_set()
                    release.set()
                    yield first
                    yield from events

            result = evaluate_stream_case(eval_case(), stream_fn=observed_stream)

            assert result["stream_succeeded"] is True
            assert result["event_types"] == [
                "answer_delta", "answer_delta", "sources", "done"
            ]
            assert result["answer_character_count"] == len("first second")
            assert model_closed.is_set()
        finally:
            release.set()
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()
