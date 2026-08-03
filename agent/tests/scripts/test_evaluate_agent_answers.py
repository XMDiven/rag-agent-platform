import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from rag_app.evaluation.judge_schema import AnswerJudgeResult

from agent_app.scripts.evaluate_agent import AgentEvalCase
from agent_app.scripts.evaluate_agent_answers import (
    evaluate_case,
    select_cases,
    summarize_case_stability,
    main,
    run_evaluation,
    summarize_results,
    write_report,
)


def eval_case() -> AgentEvalCase:
    return AgentEvalCase(
        id="rag_definition",
        question="What is RAG?",
        task_type="retrieval",
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
        "task_type": "retrieval",
        "judge_applicable": True,
        "scored_dimensions": [
            "relevance_score",
            "completeness_score",
            "groundedness_score",
            "format_score",
        ],
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
        "task_type": "retrieval",
        "judge_applicable": True,
        "failure_stage": failure_stage,
        "agent_duration_seconds": agent_duration,
        "judge_duration_seconds": judge_duration,
        "total_duration_seconds": agent_duration + judge_duration,
    }


def test_summarize_results_returns_zero_metrics_for_empty_cases() -> None:
    assert summarize_results([]) == {
        "total": 0,
        "judged_total": 0,
        "skipped_total": 0,
        "passed": 0,
        "failed": 0,
        "pass_rate": 0.0,
        "pass_rate_by_task_type": {},
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
        "judged_total": 3,
        "skipped_total": 0,
        "passed": 2,
        "failed": 1,
        "pass_rate": 0.667,
        "pass_rate_by_task_type": {
            "retrieval": {"total": 3, "passed": 2, "pass_rate": 0.667},
        },
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
            task_type="retrieval",
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
    assert "Evaluating Agent Judge case 1/2 (run 1/1): rag_definition" in output
    assert "completed rag_definition passed=True" in output
    assert "Evaluating Agent Judge case 2/2 (run 1/1): qdrant_purpose" in output
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
        "summary": {"total": 1, "judged_total": 1, "skipped_total": 0, "passed": 1, "failed": 0},
        "cases": [],
    }

    path = write_report(report, output_dir=tmp_path / "nested" / "judge")

    assert path.name == "20260803-120000.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report


def test_main_returns_nonzero_when_any_case_fails(tmp_path, monkeypatch) -> None:
    report = {
        "run_id": "20260803-120000",
        "summary": {"total": 2, "judged_total": 2, "skipped_total": 0, "passed": 1, "failed": 1},
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
        lambda cases, judge_llm, judge_model_id, repeat: report,
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
        "summary": {"total": 1, "judged_total": 1, "skipped_total": 0, "passed": 1, "failed": 0},
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
        lambda cases, judge_llm, judge_model_id, repeat: report,
    )
    monkeypatch.setattr(
        "agent_app.scripts.evaluate_agent_answers.write_report",
        lambda result, output_dir: tmp_path / "report.json",
    )

    assert main(["--cases", "cases.json", "--output-dir", str(tmp_path)]) == 0


def summary_case() -> AgentEvalCase:
    return AgentEvalCase(
        id="summary_literal_text",
        question="请总结这段文字：RAG 先检索，再生成。",
        task_type="summary",
        allowed_tools=["summary_tool"],
        required_tools=["summary_tool"],
        allowed_termination_reasons=["final_answer"],
        requires_sources=False,
        max_steps=4,
    )


def input_validation_case() -> AgentEvalCase:
    return AgentEvalCase(
        id="empty_question",
        question="",
        task_type="input_validation",
        allowed_tools=["fallback_tool"],
        required_tools=["fallback_tool"],
        allowed_termination_reasons=["single_step"],
        requires_sources=False,
        max_steps=0,
    )


def test_summary_case_is_judged_against_the_user_supplied_text() -> None:
    judge_calls: list[list[dict[str, object]]] = []

    def fake_judge(question, answer, sources, llm):
        judge_calls.append(sources)
        return passing_judge_result()

    times = iter([0.0, 1.0, 1.0, 2.0])
    result = evaluate_case(
        summary_case(),
        judge_llm=object(),
        run_agent_fn=lambda question: agent_result(answer="RAG 先检索再生成。"),
        judge_fn=fake_judge,
        timer=lambda: next(times),
    )

    assert judge_calls == [
        [
            {
                "source": "user_input",
                "section_path": "question",
                "snippet": "请总结这段文字：RAG 先检索，再生成。",
            }
        ]
    ]
    assert result["task_type"] == "summary"
    assert result["judge_applicable"] is True
    assert result["passed"] is True


def test_input_validation_case_is_not_judged_for_answer_quality() -> None:
    judge_calls: list[str] = []

    def fake_judge(question, answer, sources, llm):
        judge_calls.append(question)
        return passing_judge_result()

    times = iter([0.0, 1.0])
    result = evaluate_case(
        input_validation_case(),
        judge_llm=object(),
        run_agent_fn=lambda question: agent_result(answer="请输入有效问题。"),
        judge_fn=fake_judge,
        timer=lambda: next(times),
    )

    assert judge_calls == []
    assert result["judge"] is None
    assert result["judge_applicable"] is False
    assert result["passed"] is None
    assert result["failure_stage"] is None


def test_pass_rate_excludes_cases_that_are_not_judged() -> None:
    summary = summarize_results(
        [
            {
                **quality_result(
                    passed=True,
                    judge=passing_judge_result().model_dump(),
                    failure_stage=None,
                    agent_duration=1.0,
                    judge_duration=1.0,
                ),
                "task_type": "retrieval",
            },
            {
                **quality_result(
                    passed=None,
                    judge=None,
                    failure_stage=None,
                    agent_duration=1.0,
                    judge_duration=0.0,
                ),
                "task_type": "input_validation",
                "judge_applicable": False,
            },
        ]
    )

    assert summary["total"] == 2
    assert summary["judged_total"] == 1
    assert summary["skipped_total"] == 1
    assert summary["pass_rate"] == 1.0
    assert summary["pass_rate_by_task_type"] == {
        "retrieval": {"total": 1, "passed": 1, "pass_rate": 1.0},
    }


def direct_case() -> AgentEvalCase:
    return AgentEvalCase(
        id="direct_greeting",
        question="请用一句话打个招呼。",
        task_type="direct",
        allowed_tools=[],
        required_tools=[],
        allowed_termination_reasons=["final_answer"],
        requires_sources=False,
        max_steps=4,
    )


def test_direct_case_passes_without_a_groundedness_score() -> None:
    ungrounded_greeting = AnswerJudgeResult(
        relevance_score=5,
        completeness_score=5,
        groundedness_score=3,
        format_score=5,
        overall_pass=False,
        feedback="No retrieved evidence was provided.",
    )
    times = iter([0.0, 1.0, 1.0, 2.0])

    result = evaluate_case(
        direct_case(),
        judge_llm=object(),
        run_agent_fn=lambda question: agent_result(answer="你好！"),
        judge_fn=lambda question, answer, sources, llm: ungrounded_greeting,
        timer=lambda: next(times),
    )

    assert result["passed"] is True
    assert "groundedness_score" not in result["scored_dimensions"]


def test_retrieval_case_still_requires_groundedness() -> None:
    times = iter([0.0, 1.0, 1.0, 2.0])
    ungrounded = AnswerJudgeResult(
        relevance_score=5,
        completeness_score=5,
        groundedness_score=3,
        format_score=5,
        overall_pass=False,
        feedback="The answer adds claims the evidence does not support.",
    )

    result = evaluate_case(
        eval_case(),
        judge_llm=object(),
        run_agent_fn=lambda question: agent_result(),
        judge_fn=lambda question, answer, sources, llm: ungrounded,
        timer=lambda: next(times),
    )

    assert result["passed"] is False


def judged_run(case_id: str, *, passed: bool, groundedness: int) -> dict[str, object]:
    return {
        "id": case_id,
        "task_type": "retrieval",
        "judge_applicable": True,
        "passed": passed,
        "judge": {
            "relevance_score": 5,
            "completeness_score": 4,
            "groundedness_score": groundedness,
            "format_score": 5,
            "overall_pass": passed,
            "feedback": "",
        },
    }


def test_case_stability_reports_denominator_and_score_range() -> None:
    stability = summarize_case_stability(
        [
            judged_run("chroma_vs_qdrant", passed=False, groundedness=2),
            judged_run("chroma_vs_qdrant", passed=True, groundedness=4),
            judged_run("chroma_vs_qdrant", passed=False, groundedness=3),
            {
                "id": "empty_question",
                "task_type": "input_validation",
                "judge_applicable": False,
                "passed": None,
                "judge": None,
            },
        ]
    )

    assert set(stability) == {"chroma_vs_qdrant"}
    bucket = stability["chroma_vs_qdrant"]
    assert bucket["runs"] == 3
    assert bucket["passed"] == 1
    assert bucket["pass_rate"] == 0.333
    assert bucket["scores"]["groundedness_score"] == {
        "min": 2,
        "max": 4,
        "mean": 3.0,
    }


def test_run_evaluation_repeats_every_case_and_tags_the_repetition() -> None:
    seen: list[tuple[str, int]] = []

    def fake_evaluate(case, judge_llm):
        return {
            "id": case.id,
            **quality_result(
                passed=True,
                judge=passing_judge_result().model_dump(),
                failure_stage=None,
                agent_duration=1.0,
                judge_duration=1.0,
            ),
        }

    report = run_evaluation(
        [eval_case()],
        judge_llm=object(),
        judge_model_id="kimi-k2.6",
        evaluate_case_fn=fake_evaluate,
        now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        repeat=3,
    )
    seen = [(item["id"], item["repetition"]) for item in report["cases"]]

    assert seen == [
        ("rag_definition", 1),
        ("rag_definition", 2),
        ("rag_definition", 3),
    ]
    assert report["repeat"] == 3
    assert report["case_stability"]["rag_definition"]["runs"] == 3


def test_select_cases_filters_by_id_and_rejects_unknown_ids() -> None:
    cases = [eval_case(), summary_case(), direct_case()]

    assert [case.id for case in select_cases(cases, "")] == [
        "rag_definition",
        "summary_literal_text",
        "direct_greeting",
    ]
    assert [
        case.id for case in select_cases(cases, "direct_greeting, rag_definition")
    ] == ["direct_greeting", "rag_definition"]

    with pytest.raises(ValueError, match="unknown case ids"):
        select_cases(cases, "nope")
