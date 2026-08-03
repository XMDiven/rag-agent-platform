import json
from datetime import datetime, timezone
from types import SimpleNamespace

from rag_app.evaluation.judge_schema import AnswerJudgeResult

from agent_app.scripts.evaluate_agent import AgentEvalCase
from agent_app.scripts.evaluate_agent_answers import (
    evaluate_case,
    main,
    run_evaluation,
    summarize_results,
    write_report,
)


def eval_case() -> AgentEvalCase:
    return AgentEvalCase(
        id="rag_definition",
        question="What is RAG?",
        allowed_tools=["retrieval_tool"],
        required_tools=["retrieval_tool"],
        allowed_termination_reasons=["final_answer"],
        requires_sources=True,
        max_steps=4,
    )


def agent_result(*, answer: str = "RAG grounds answers with retrieved context."):
    return SimpleNamespace(
        termination_reason="final_answer",
        steps=[
            {
                "round": 1,
                "status": "tool_executed",
                "tool_name": "retrieval_tool",
            },
            {
                "round": 2,
                "status": "final_answer",
                "tool_name": None,
            },
        ],
        plan=SimpleNamespace(tool=SimpleNamespace(name="retrieval_tool")),
        tool_result=SimpleNamespace(
            output={
                "answer": answer,
                "sources": [
                    {
                        "source": "data/raw/rag.md",
                        "section_path": "Introduction",
                        "snippet": "RAG combines retrieval and generation.",
                    }
                ],
            }
        ),
    )


def passing_judge_result() -> AnswerJudgeResult:
    return AnswerJudgeResult(
        relevance_score=5,
        completeness_score=4,
        groundedness_score=5,
        format_score=4,
        overall_pass=True,
        feedback="The answer is supported by the evidence.",
    )


def test_evaluate_case_judges_agent_answer_once_and_sanitizes_sources() -> None:
    agent_calls: list[str] = []
    judge_calls: list[dict[str, object]] = []

    def fake_run_agent(question: str):
        agent_calls.append(question)
        return agent_result()

    def fake_judge(question, answer, sources, llm):
        judge_calls.append(
            {
                "question": question,
                "answer": answer,
                "sources": sources,
                "llm": llm,
            }
        )
        return passing_judge_result()

    times = iter([0.0, 2.0, 2.0, 3.0])
    judge_llm = object()

    result = evaluate_case(
        eval_case(),
        judge_llm=judge_llm,
        run_agent_fn=fake_run_agent,
        judge_fn=fake_judge,
        timer=lambda: next(times),
    )

    assert agent_calls == ["What is RAG?"]
    assert judge_calls == [
        {
            "question": "What is RAG?",
            "answer": "RAG grounds answers with retrieved context.",
            "sources": [
                {
                    "source": "data/raw/rag.md",
                    "section_path": "Introduction",
                    "snippet": "RAG combines retrieval and generation.",
                }
            ],
            "llm": judge_llm,
        }
    ]
    assert result == {
        "id": "rag_definition",
        "question": "What is RAG?",
        "answer": "RAG grounds answers with retrieved context.",
        "sources": [
            {
                "source": "data/raw/rag.md",
                "section_path": "Introduction",
            }
        ],
        "termination_reason": "final_answer",
        "tools": ["retrieval_tool"],
        "step_count": 2,
        "judge": {
            "relevance_score": 5,
            "completeness_score": 4,
            "groundedness_score": 5,
            "format_score": 4,
            "overall_pass": True,
            "feedback": "The answer is supported by the evidence.",
        },
        "passed": True,
        "failure_stage": None,
        "error": None,
        "agent_duration_seconds": 2.0,
        "judge_duration_seconds": 1.0,
        "total_duration_seconds": 3.0,
    }


def test_evaluate_case_isolates_agent_error_without_persisting_message() -> None:
    judge_called = False

    def fail_agent(question: str):
        raise RuntimeError("api_key=agent-secret")

    def fail_if_judged(**kwargs):
        nonlocal judge_called
        judge_called = True
        raise AssertionError("Judge must not run after Agent failure")

    times = iter([0.0, 1.0])
    result = evaluate_case(
        eval_case(),
        judge_llm=object(),
        run_agent_fn=fail_agent,
        judge_fn=fail_if_judged,
        timer=lambda: next(times),
    )

    assert not judge_called
    assert result["passed"] is False
    assert result["failure_stage"] == "agent"
    assert result["error"] == {"type": "RuntimeError"}
    assert result["judge"] is None
    assert result["agent_duration_seconds"] == 1.0
    assert result["judge_duration_seconds"] == 0.0
    assert "agent-secret" not in json.dumps(result)


def test_evaluate_case_skips_judge_for_empty_answer() -> None:
    judge_called = False

    def fail_if_judged(**kwargs):
        nonlocal judge_called
        judge_called = True
        raise AssertionError("Judge must not run for an empty answer")

    times = iter([0.0, 1.0])
    result = evaluate_case(
        eval_case(),
        judge_llm=object(),
        run_agent_fn=lambda question: agent_result(answer="   "),
        judge_fn=fail_if_judged,
        timer=lambda: next(times),
    )

    assert not judge_called
    assert result["passed"] is False
    assert result["failure_stage"] == "empty_answer"
    assert result["error"] is None
    assert result["judge"] is None
    assert result["answer"] == "   "


def test_evaluate_case_isolates_judge_error_without_persisting_message() -> None:
    def fail_judge(**kwargs):
        raise ValueError("token=judge-secret")

    times = iter([0.0, 2.0, 2.0, 3.0])
    result = evaluate_case(
        eval_case(),
        judge_llm=object(),
        run_agent_fn=lambda question: agent_result(),
        judge_fn=fail_judge,
        timer=lambda: next(times),
    )

    assert result["passed"] is False
    assert result["failure_stage"] == "judge"
    assert result["error"] == {"type": "ValueError"}
    assert result["judge"] is None
    assert result["sources"] == [
        {
            "source": "data/raw/rag.md",
            "section_path": "Introduction",
        }
    ]
    assert result["agent_duration_seconds"] == 2.0
    assert result["judge_duration_seconds"] == 1.0
    assert "judge-secret" not in json.dumps(result)


def quality_result(
    *,
    passed: bool,
    judge: dict[str, object] | None,
    failure_stage: str | None,
    agent_duration: float,
    judge_duration: float,
) -> dict[str, object]:
    return {
        "passed": passed,
        "judge": judge,
        "failure_stage": failure_stage,
        "agent_duration_seconds": agent_duration,
        "judge_duration_seconds": judge_duration,
        "total_duration_seconds": agent_duration + judge_duration,
    }


def test_summarize_results_returns_zero_metrics_for_empty_cases() -> None:
    assert summarize_results([]) == {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "pass_rate": 0.0,
        "average_scores": {
            "relevance": 0.0,
            "completeness": 0.0,
            "groundedness": 0.0,
            "format": 0.0,
        },
        "average_agent_duration_seconds": 0.0,
        "p95_agent_duration_seconds": 0.0,
        "average_judge_duration_seconds": 0.0,
        "p95_judge_duration_seconds": 0.0,
        "average_total_duration_seconds": 0.0,
        "p95_total_duration_seconds": 0.0,
        "agent_failed_count": 0,
        "judge_failed_count": 0,
    }


def test_summarize_results_uses_valid_judges_for_score_averages() -> None:
    results = [
        quality_result(
            passed=True,
            judge={
                "relevance_score": 5,
                "completeness_score": 4,
                "groundedness_score": 5,
                "format_score": 4,
            },
            failure_stage=None,
            agent_duration=1.0,
            judge_duration=2.0,
        ),
        quality_result(
            passed=True,
            judge={
                "relevance_score": 4,
                "completeness_score": 4,
                "groundedness_score": 4,
                "format_score": 4,
            },
            failure_stage=None,
            agent_duration=2.0,
            judge_duration=3.0,
        ),
        quality_result(
            passed=False,
            judge=None,
            failure_stage="judge",
            agent_duration=3.0,
            judge_duration=4.0,
        ),
    ]

    assert summarize_results(results) == {
        "total": 3,
        "passed": 2,
        "failed": 1,
        "pass_rate": 0.667,
        "average_scores": {
            "relevance": 4.5,
            "completeness": 4.0,
            "groundedness": 4.5,
            "format": 4.0,
        },
        "average_agent_duration_seconds": 2.0,
        "p95_agent_duration_seconds": 3.0,
        "average_judge_duration_seconds": 3.0,
        "p95_judge_duration_seconds": 4.0,
        "average_total_duration_seconds": 5.0,
        "p95_total_duration_seconds": 7.0,
        "agent_failed_count": 0,
        "judge_failed_count": 1,
    }


def test_summarize_results_excludes_unattempted_judges_from_latency() -> None:
    results = [
        quality_result(
            passed=True,
            judge={
                "relevance_score": 5,
                "completeness_score": 5,
                "groundedness_score": 5,
                "format_score": 5,
            },
            failure_stage=None,
            agent_duration=1.0,
            judge_duration=10.0,
        ),
        quality_result(
            passed=False,
            judge=None,
            failure_stage="agent",
            agent_duration=2.0,
            judge_duration=0.0,
        ),
        quality_result(
            passed=False,
            judge=None,
            failure_stage="empty_answer",
            agent_duration=3.0,
            judge_duration=0.0,
        ),
    ]

    summary = summarize_results(results)

    assert summary["average_judge_duration_seconds"] == 10.0
    assert summary["p95_judge_duration_seconds"] == 10.0


def test_run_evaluation_runs_all_cases_and_prints_progress(capsys) -> None:
    cases = [
        eval_case(),
        AgentEvalCase(
            id="qdrant_purpose",
            question="What is Qdrant used for?",
            allowed_tools=["retrieval_tool"],
            required_tools=["retrieval_tool"],
            allowed_termination_reasons=["final_answer"],
            requires_sources=True,
            max_steps=4,
        ),
    ]
    calls: list[dict[str, object]] = []

    def fake_evaluate(case, judge_llm):
        calls.append({"id": case.id, "judge_llm": judge_llm})
        return {
            "id": case.id,
            **quality_result(
                passed=case.id == "rag_definition",
                judge=(
                    {
                        "relevance_score": 5,
                        "completeness_score": 4,
                        "groundedness_score": 5,
                        "format_score": 4,
                    }
                    if case.id == "rag_definition"
                    else None
                ),
                failure_stage=(
                    None if case.id == "rag_definition" else "judge"
                ),
                agent_duration=1.0,
                judge_duration=2.0,
            ),
        }

    judge_llm = object()
    report = run_evaluation(
        cases,
        judge_llm=judge_llm,
        judge_model_id="kimi-k2.6",
        evaluate_case_fn=fake_evaluate,
        now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )
    output = capsys.readouterr().out

    assert calls == [
        {"id": "rag_definition", "judge_llm": judge_llm},
        {"id": "qdrant_purpose", "judge_llm": judge_llm},
    ]
    assert "Evaluating Agent Judge case 1/2: rag_definition" in output
    assert "completed rag_definition passed=True" in output
    assert "Evaluating Agent Judge case 2/2: qdrant_purpose" in output
    assert report["run_id"] == "20260803-120000"
    assert report["judge_model_id"] == "kimi-k2.6"
    assert report["judge_independence"] == "same_model"
    assert report["summary"]["passed"] == 1
    assert [item["id"] for item in report["cases"]] == [
        "rag_definition",
        "qdrant_purpose",
    ]


def test_write_report_creates_output_directory_and_round_trips_json(tmp_path) -> None:
    report = {
        "run_id": "20260803-120000",
        "summary": {"total": 1, "passed": 1, "failed": 0},
        "cases": [],
    }

    path = write_report(report, output_dir=tmp_path / "nested" / "judge")

    assert path.name == "20260803-120000.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report


def test_main_returns_nonzero_when_any_case_fails(tmp_path, monkeypatch) -> None:
    report = {
        "run_id": "20260803-120000",
        "summary": {"total": 2, "passed": 1, "failed": 1},
        "cases": [],
    }
    judge_llm = object()
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_answers.load_cases",
        lambda path: [eval_case()],
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_answers.get_client",
        lambda: judge_llm,
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_answers.run_evaluation",
        lambda cases, judge_llm, judge_model_id: report,
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_answers.write_report",
        lambda result, output_dir: tmp_path / "report.json",
    )

    exit_code = main(
        ["--cases", "cases.json", "--output-dir", str(tmp_path)]
    )

    assert exit_code == 1


def test_main_returns_zero_when_all_cases_pass(tmp_path, monkeypatch) -> None:
    report = {
        "run_id": "20260803-120000",
        "summary": {"total": 1, "passed": 1, "failed": 0},
        "cases": [],
    }
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_answers.load_cases",
        lambda path: [eval_case()],
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_answers.get_client",
        lambda: object(),
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_answers.run_evaluation",
        lambda cases, judge_llm, judge_model_id: report,
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_answers.write_report",
        lambda result, output_dir: tmp_path / "report.json",
    )

    assert main(["--cases", "cases.json", "--output-dir", str(tmp_path)]) == 0
