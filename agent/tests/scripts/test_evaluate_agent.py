import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent_app.scripts.evaluate_agent import (
    AgentEvalCase,
    evaluate_result,
    extract_tool_names,
    load_cases,
    main,
    nearest_rank_percentile,
    run_evaluation,
    summarize_results,
    write_report,
)


def valid_case(**overrides: object) -> dict[str, object]:
    case: dict[str, object] = {
        "id": "retrieval_case",
        "question": "What is RAG?",
        "allowed_tools": ["retrieval_tool"],
        "required_tools": ["retrieval_tool"],
        "allowed_termination_reasons": ["final_answer"],
        "requires_sources": True,
        "max_steps": 4,
    }
    case.update(overrides)
    return case


def write_cases(tmp_path, cases: object):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    return path


def test_load_cases_returns_typed_cases(tmp_path) -> None:
    path = write_cases(tmp_path, [valid_case()])

    assert load_cases(path) == [
        AgentEvalCase(
            id="retrieval_case",
            question="What is RAG?",
            allowed_tools=["retrieval_tool"],
            required_tools=["retrieval_tool"],
            allowed_termination_reasons=["final_answer"],
            requires_sources=True,
            max_steps=4,
        )
    ]


@pytest.mark.parametrize(
    ("cases", "message"),
    [
        ({"id": "not-a-list"}, "dataset root"),
        ([valid_case(), valid_case()], "duplicate case id"),
        ([valid_case(unexpected=True)], "unknown fields"),
        ([valid_case(allowed_tools=["retrieval_tool", 3])], "allowed_tools"),
        ([valid_case(max_steps=-1)], "max_steps"),
        ([valid_case(max_steps=True)], "max_steps"),
        ([valid_case(allowed_termination_reasons=[])], "allowed_termination_reasons"),
        (
            [valid_case(allowed_tools=[], required_tools=["retrieval_tool"])],
            "required_tools",
        ),
    ],
)
def test_load_cases_rejects_invalid_contract(
    tmp_path,
    cases: object,
    message: str,
) -> None:
    path = write_cases(tmp_path, cases)

    with pytest.raises(ValueError, match=message):
        load_cases(path)


def fake_result(
    *,
    steps=None,
    termination_reason="final_answer",
    selected_tool="retrieval_tool",
    sources=None,
):
    return SimpleNamespace(
        steps=steps or [],
        termination_reason=termination_reason,
        plan=SimpleNamespace(tool=SimpleNamespace(name=selected_tool)),
        tool_result=SimpleNamespace(output={"sources": sources or []}),
    )


def test_extract_tool_names_preserves_loop_call_order() -> None:
    result = fake_result(
        steps=[
            {"round": 1, "status": "tool_executed", "tool_name": "retrieval_tool"},
            {"round": 2, "status": "final_answer", "tool_name": None},
            {"round": 3, "status": "tool_failed", "tool_name": "summary_tool"},
        ]
    )

    assert extract_tool_names(result) == ["retrieval_tool", "summary_tool"]


def test_extract_tool_names_uses_plan_for_single_step_result() -> None:
    result = fake_result(
        termination_reason="single_step",
        selected_tool="fallback_tool",
    )

    assert extract_tool_names(result) == ["fallback_tool"]


@pytest.mark.parametrize(
    ("case_overrides", "result", "failure_reason"),
    [
        (
            {},
            fake_result(
                steps=[{"tool_name": "summary_tool"}],
                selected_tool="summary_tool",
            ),
            "disallowed_tool",
        ),
        (
            {},
            fake_result(steps=[]),
            "missing_required_tool",
        ),
        (
            {},
            fake_result(
                steps=[{"tool_name": "retrieval_tool"}],
                termination_reason="max_steps",
            ),
            "unexpected_termination_reason",
        ),
        (
            {},
            fake_result(
                steps=[{"tool_name": "retrieval_tool"}],
                sources=[],
            ),
            "missing_sources",
        ),
        (
            {"max_steps": 1},
            fake_result(
                steps=[
                    {"tool_name": "retrieval_tool"},
                    {"tool_name": None},
                ],
                sources=[{"source": "rag.md"}],
            ),
            "max_steps_exceeded",
        ),
    ],
)
def test_evaluate_result_reports_independent_failures(
    case_overrides: dict[str, object],
    result,
    failure_reason: str,
) -> None:
    case = AgentEvalCase(**valid_case(**case_overrides))

    evaluation = evaluate_result(case, result)

    assert not evaluation["passed"]
    assert failure_reason in evaluation["failure_reasons"]


def test_evaluate_result_passes_when_all_constraints_match() -> None:
    case = AgentEvalCase(**valid_case())
    result = fake_result(
        steps=[
            {"round": 1, "tool_name": "retrieval_tool"},
            {"round": 2, "tool_name": None},
        ],
        sources=[{"source": "data/raw/rag.md"}],
    )

    evaluation = evaluate_result(case, result)

    assert evaluation["passed"]
    assert evaluation["failure_reasons"] == []
    assert evaluation["actual"] == {
        "tools": ["retrieval_tool"],
        "termination_reason": "final_answer",
        "source_count": 1,
        "step_count": 2,
    }
    assert all(evaluation["checks"].values())


def test_nearest_rank_percentile_returns_observed_value() -> None:
    assert nearest_rank_percentile([0.1, 0.2, 0.3, 0.4], 0.95) == 0.4
    assert nearest_rank_percentile([], 0.95) == 0.0


def test_summarize_results_returns_zero_metrics_for_empty_input() -> None:
    assert summarize_results([]) == {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "pass_rate": 0.0,
        "normal_termination_rate": 0.0,
        "tool_constraint_pass_rate": 0.0,
        "source_constraint_pass_rate": 0.0,
        "average_latency_seconds": 0.0,
        "p95_latency_seconds": 0.0,
    }


def test_summarize_results_calculates_quality_and_latency_metrics() -> None:
    results = [
        {
            "passed": True,
            "latency_seconds": 0.1,
            "actual": {"termination_reason": "final_answer"},
            "checks": {"tools": True, "sources": True},
        },
        {
            "passed": True,
            "latency_seconds": 0.2,
            "actual": {"termination_reason": "single_step"},
            "checks": {"tools": True, "sources": True},
        },
        {
            "passed": False,
            "latency_seconds": 0.3,
            "actual": {"termination_reason": "max_steps"},
            "checks": {"tools": False, "sources": True},
        },
        {
            "passed": False,
            "latency_seconds": 0.4,
            "actual": {"termination_reason": "failed"},
            "checks": {"tools": True, "sources": False},
        },
    ]

    assert summarize_results(results) == {
        "total": 4,
        "passed": 2,
        "failed": 2,
        "pass_rate": 0.5,
        "normal_termination_rate": 0.5,
        "tool_constraint_pass_rate": 0.75,
        "source_constraint_pass_rate": 0.75,
        "average_latency_seconds": 0.25,
        "p95_latency_seconds": 0.4,
    }


def test_run_evaluation_continues_after_case_error() -> None:
    cases = [
        AgentEvalCase(**valid_case(id="success_case")),
        AgentEvalCase(
            **valid_case(
                id="error_case",
                question="Summarize this text",
                allowed_tools=["summary_tool"],
                required_tools=["summary_tool"],
                requires_sources=False,
            )
        ),
    ]
    questions: list[str] = []

    def fake_run_agent(question: str):
        questions.append(question)
        if question == "Summarize this text":
            raise RuntimeError("model unavailable")
        return fake_result(
            steps=[{"tool_name": "retrieval_tool"}],
            sources=[{"source": "rag.md"}],
        )

    times = iter([1.0, 1.2, 2.0, 2.5])

    report = run_evaluation(
        cases,
        run_agent_fn=fake_run_agent,
        timer=lambda: next(times),
        now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert questions == ["What is RAG?", "Summarize this text"]
    assert report["generated_at"] == "2026-08-03T12:00:00Z"
    assert report["summary"]["total"] == 2
    assert report["summary"]["passed"] == 1
    assert report["cases"][0]["latency_seconds"] == 0.2
    assert report["cases"][1]["latency_seconds"] == 0.5
    assert report["cases"][1]["failure_reasons"] == ["agent_error"]
    assert report["cases"][1]["error"] == {
        "type": "RuntimeError",
        "message": "model unavailable",
    }


def test_write_report_creates_directory_and_round_trips_json(tmp_path) -> None:
    report = {"generated_at": "2026-08-03T12:00:00Z", "summary": {"failed": 0}}

    path = write_report(
        report,
        output_dir=tmp_path / "nested" / "runs",
        now=datetime(2026, 8, 3, 12, 34, 56, tzinfo=timezone.utc),
    )

    assert path.name == "20260803-123456.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report


@pytest.mark.parametrize(("failed", "exit_code"), [(0, 0), (1, 1)])
def test_main_returns_exit_code_from_failed_case_count(
    tmp_path,
    monkeypatch,
    failed: int,
    exit_code: int,
) -> None:
    report = {
        "generated_at": "2026-08-03T12:00:00Z",
        "summary": {"total": 2, "passed": 2 - failed, "failed": failed},
        "cases": [],
    }
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent.load_cases",
        lambda path: [],
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent.run_evaluation",
        lambda cases: report,
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent.write_report",
        lambda result, output_dir: tmp_path / "report.json",
    )

    assert main(["--cases", "cases.json", "--output-dir", str(tmp_path)]) == exit_code


def test_default_dataset_has_representative_tool_coverage() -> None:
    cases = load_cases()

    assert len(cases) == 12
    assert len({case.id for case in cases}) == 12
    covered_tools = {
        tool
        for case in cases
        for tool in case.allowed_tools
    }
    assert covered_tools >= {
        "fallback_tool",
        "summary_tool",
        "retrieval_tool",
        "question_decompose_tool",
    }
    assert any(case.requires_sources for case in cases)
