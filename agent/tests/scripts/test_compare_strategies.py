import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from agent_app.scripts import compare_strategies as comparison
from rag_app.evaluation.judge_schema import AnswerJudgeResult
from rag_app.infrastructure.token_usage import get_request_usage
from rag_app.scripts.evaluate_retrieval import RetrievalEvalCase


def case():
    return RetrievalEvalCase("existing_case", "比较两个框架", ["one", "two"], "all")


def sources():
    return [
        {"source": "one.md", "section_path": "intro", "snippet": "First original evidence."},
        {"source": "two.md", "section_path": "use cases", "snippet": "Second original evidence."},
    ]


def judge_result():
    return AnswerJudgeResult(
        relevance_score=5, completeness_score=4, groundedness_score=4,
        format_score=5, overall_pass=True, feedback="offline test",
    )


def test_fixed_workflow_synthesizes_original_evidence_once(monkeypatch):
    monkeypatch.setattr(comparison, "run_decomposed_retrieval", lambda question: {
        "answer": "Generated intermediate answer with local citations [1].",
        "sources": sources(),
        "sub_questions": ["first", "second"],
        "sub_results": [
            {"status": "success", "answer": "first", "sources": sources()[:1]},
            {"status": "success", "answer": "second", "sources": sources()[1:]},
        ],
    })
    prompts = []

    def model(prompt):
        prompts.append(prompt.to_messages()[-1].content)
        return "Combined answer [1][2]."

    monkeypatch.setattr(comparison, "get_client", lambda: RunnableLambda(model))
    output = comparison.run_fixed_workflow(case().question)

    assert output["answer"] == "Combined answer [1][2]."
    assert output["sources"] == sources()
    assert output["sub_questions"] == ["first", "second"]
    assert len(prompts) == 1
    assert case().question in prompts[0]
    assert "[1]\nsource: one.md" in prompts[0]
    assert "[2]\nsource: two.md" in prompts[0]
    assert "First original evidence." in prompts[0]
    assert "Second original evidence." in prompts[0]
    assert "Generated intermediate answer" not in prompts[0]


@pytest.mark.parametrize("failed_part", ["subquestion", "missing_evidence"])
def test_fixed_workflow_does_not_synthesize_failed_or_evidenceless_results(monkeypatch, failed_part):
    monkeypatch.setattr(comparison, "run_decomposed_retrieval", lambda question: {
        "sources": sources() if failed_part == "subquestion" else [],
        "sub_questions": ["first"],
        "sub_results": [{"status": "failed" if failed_part == "subquestion" else "success"}],
    })

    def forbidden():
        pytest.fail("Must not start another model call")

    monkeypatch.setattr(comparison, "get_client", forbidden)
    with pytest.raises(RuntimeError):
        comparison.run_fixed_workflow(case().question)


def test_fixed_workflow_rejects_partial_evidence_even_if_tool_marks_success(monkeypatch):
    monkeypatch.setattr(comparison, "run_decomposed_retrieval", lambda question: {
        "sources": sources()[:1],
        "sub_questions": ["first", "second"],
        "sub_results": [
            {"status": "success", "answer": "first", "sources": sources()[:1]},
            {"status": "success", "answer": comparison.config.FALLBACK_ANSWER, "sources": []},
        ],
    })
    monkeypatch.setattr(comparison, "get_client", lambda: pytest.fail("Missing evidence must not trigger synthesis"))
    with pytest.raises(RuntimeError):
        comparison.run_fixed_workflow(case().question)


def test_comparison_judges_each_same_run_and_separates_usage_with_cache_disabled(monkeypatch, tmp_path):
    calls, judged = [], []
    original_getter = lambda: object()
    monkeypatch.setattr(comparison.ask_service, "get_answer_cache", original_getter)

    def ask(question):
        assert comparison.ask_service.get_answer_cache() is None
        get_request_usage().add(100, 10, 20)
        return {"answer": "answer", "sources": sources()}

    monkeypatch.setattr(comparison.ask_service, "ask_question", ask)

    def execute(question, strategy):
        calls.append((question, strategy))
        # Exercise both ask_service and the Agent tool's imported alias.
        ask_fn = comparison.retrieval_tool.ask_question if strategy == "agent" else comparison.ask_service.ask_question
        output = ask_fn(question)
        return {**output, "answer": strategy, "tools": ["retrieval_tool"] if strategy == "agent" else [], "termination_reason": "final_answer"}

    def judge(**kwargs):
        judged.append((kwargs["question"], kwargs["answer"], kwargs["sources"]))
        get_request_usage().add(11, 0, 2)
        return judge_result()

    report = comparison.run_comparison(
        [case()], judge_llm=object(), run_strategy_fn=execute, judge_fn=judge,
        now=datetime(2026, 10, 24, tzinfo=timezone.utc),
    )
    assert calls == [(case().question, strategy) for strategy in comparison.STRATEGIES]
    assert judged == [(case().question, strategy, sources()) for strategy in comparison.STRATEGIES]
    assert comparison.ask_service.get_answer_cache is original_getter
    assert report["configuration"]["answer_cache"] is False
    assert report["summary"] == {"total": 3, "passed": 3, "failed": 0}
    for row in report["cases"]:
        assert row["passed"] is True
        assert row["llm_calls"] == 1
        assert row["input_tokens"] == 100
        assert row["cached_input_tokens"] == 10
        assert row["output_tokens"] == 20
        assert row["rag_calls"] == 1
        assert row["retrieval_calls"] == 0  # QA stub performs no retrieval operation.
        assert row["judge_usage"] == {"llm_calls": 1, "input_tokens": 11, "cached_input_tokens": 0, "output_tokens": 2}
        assert "Second original evidence." in row["judge_evidence"]
    path = comparison.write_report(report, output_dir=tmp_path)
    assert json.loads(path.read_text()) == report


@pytest.mark.parametrize("failure", ["execution", "judge", "empty_answer", "missing_sources", "failed_termination"])
def test_one_failed_strategy_is_counted_and_does_not_stop_other_strategies(failure):
    def execute(question, strategy):
        if strategy == "rag" and failure == "execution":
            raise RuntimeError("api_key=private-execution")
        return {
            "answer": "" if strategy == "rag" and failure == "empty_answer" else strategy,
            "sources": [] if strategy == "rag" and failure == "missing_sources" else sources(),
            "tools": [],
            "termination_reason": "failed" if strategy == "rag" and failure == "failed_termination" else "final_answer",
        }

    judged = []

    def judge(**kwargs):
        judged.append(kwargs["answer"])
        if kwargs["answer"] == "rag" and failure == "judge":
            raise ValueError("token=private-judge")
        return judge_result()

    report = comparison.run_comparison([case()], object(), run_strategy_fn=execute, judge_fn=judge)
    assert report["summary"] == {"total": 3, "passed": 2, "failed": 1}
    assert report["cases"][0]["passed"] is False
    assert report["cases"][0]["failure_stage"] == failure
    assert "private-" not in json.dumps(report)
    assert all(row["passed"] for row in report["cases"][1:])
    if failure != "judge":
        assert judged == ["fixed", "agent"]


def test_agent_strategy_adapts_real_agent_result_without_rerunning(monkeypatch):
    calls = []

    def agent(question):
        calls.append(question)
        return SimpleNamespace(
            tool_result=SimpleNamespace(output={"answer": "Agent answer", "sources": sources()}, status="success"),
            steps=[{"tool_name": "question_decompose_tool"}, {"tool_name": None}],
            termination_reason="final_answer",
        )

    monkeypatch.setattr(comparison, "run_agent", agent)
    output = comparison.run_strategy(case().question, "agent")
    assert calls == [case().question]
    assert output["answer"] == "Agent answer"
    assert output["sources"] == sources()
    assert output["tools"] == ["question_decompose_tool"]


def test_default_preview_uses_existing_case_without_creating_model(monkeypatch, capsys):
    def forbidden():
        pytest.fail("Preview must not create a model")

    monkeypatch.setattr(comparison, "get_client", forbidden)
    assert comparison.main([]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "preview"
    assert preview["strategies"] == ["rag", "fixed", "agent"]
    assert len(preview["cases"]) == 1
    assert preview["cases"][0]["id"] == "langchain_llamaindex_comparison"
    assert preview["cases"][0]["fixed_sub_questions"] == ["LangChain 适合做什么？", "LlamaIndex 适合做什么？"]


def test_unknown_case_is_rejected_before_model_creation(monkeypatch):
    monkeypatch.setattr(comparison, "get_client", lambda: pytest.fail("Must validate selection first"))
    with pytest.raises(ValueError, match="unknown"):
        comparison.main(["--only", "does-not-exist", "--run"])


def test_cli_runs_selected_case_once_and_returns_failure_status(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(comparison, "get_client", lambda: object())

    def compare(cases, **kwargs):
        assert kwargs["repeats"] == 1
        calls.append([item.id for item in cases])
        return {"run_id": "offline-comparison", "summary": {"total": 3, "passed": 2, "failed": 1}, "cases": []}

    monkeypatch.setattr(comparison, "run_comparison", compare)
    assert comparison.main(["--run", "--output-dir", str(tmp_path)]) == 1
    assert calls == [["langchain_llamaindex_comparison"]]
    assert (tmp_path / "offline-comparison.json").exists()


def test_three_actual_paths_run_offline_on_the_existing_comparison_question(
    monkeypatch, patch_loop_llm, make_tool_call,
):
    existing_case = next(c for c in comparison.load_case() if c.id == comparison.DEFAULT_CASE_ID)
    questions = []

    def ask(question):
        questions.append(question)
        selected = sources()
        if question == "LangChain 适合做什么？":
            selected = selected[:1]
        elif question == "LlamaIndex 适合做什么？":
            selected = selected[1:]
        return {"answer": "Retrieved answer [1].", "sources": selected}

    monkeypatch.setattr(comparison.ask_service, "ask_question", ask)
    monkeypatch.setattr(comparison, "get_client", lambda: RunnableLambda(lambda prompt: "Fixed answer [1][2]."))
    patch_loop_llm([
        make_tool_call("question_decompose_tool", {"question": existing_case.question}),
        AIMessage(content="Agent answer [1][2]."),
    ])

    report = comparison.run_comparison([existing_case], object(), judge_fn=lambda **kwargs: judge_result())
    assert [row["answer"] for row in report["cases"]] == [
        "Retrieved answer [1].", "Fixed answer [1][2].", "Agent answer [1][2].",
    ]
    assert [row["rag_calls"] for row in report["cases"]] == [1, 2, 2]
    assert [row["tool_calls"] for row in report["cases"]] == [0, 0, 1]
    assert all(row["sources"] == sources() for row in report["cases"])
    assert report["summary"] == {"total": 3, "passed": 3, "failed": 0}
    assert questions == [existing_case.question] + ["LangChain 适合做什么？", "LlamaIndex 适合做什么？"] * 2


def test_repeat_statistics_include_failed_rows_and_distinguish_qa_from_retrieval(monkeypatch):
    from langchain_core.documents import Document
    monkeypatch.setattr(comparison.ask_service, "retrieve_documents_with_retry", lambda *args, **kwargs: [
        Document(page_content="original", metadata={"source": "one.md"}),
    ])

    def ask(question):
        plan = SimpleNamespace(retrieval_query=question, retrieval_strategy="standard_retrieval", top_k=7)
        docs = comparison.ask_service.retrieve_documents_with_retry(plan, [])
        return {"answer": "answer", "sources": comparison.ask_service.build_sources(docs)}

    monkeypatch.setattr(comparison.ask_service, "ask_question", ask)
    report = comparison.run_comparison(
        [case()], object(), repeats=2,
        run_strategy_fn=lambda question, strategy: comparison.ask_service.ask_question(question),
        judge_fn=lambda **kwargs: judge_result(),
    )
    assert len(report["cases"]) == 6
    assert [row["repeat"] for row in report["cases"]] == [1, 1, 1, 2, 2, 2]
    assert [row["strategy"] for row in report["cases"]] == ["rag", "fixed", "agent", "fixed", "agent", "rag"]
    assert report["statistics"]["by_strategy"]["rag"]["total"] == 2
    for row in report["cases"]:
        assert row["rag_calls"] == row["retrieval_calls"] == 1
        assert row["retrieval_trace"][0]["sources"][0]["snippet"] == "original"
        assert row["cost"] is None
        assert row["completed"]
        assert "expected_source_missing" in row["diagnosis_candidates"]


def test_swallowed_budget_failure_is_still_failed_and_never_judged(monkeypatch):
    scopes = []
    original = comparison.ModelBudget

    def budget(*args, **kwargs):
        scope = original(*args, **kwargs)
        scopes.append(scope)
        return scope

    monkeypatch.setattr(comparison, "ModelBudget", budget)

    def execute(question, strategy):
        try:
            scopes[-1]._fail("calls")
        except comparison.BudgetExceeded:
            pass
        return {"answer": "caught fallback", "sources": sources()}

    report = comparison.run_comparison(
        [case()], object(), run_strategy_fn=execute,
        judge_fn=lambda **kwargs: pytest.fail("Budget failure must skip Judge"),
    )
    assert report["summary"]["failed"] == 3
    assert all(row["failure_stage"] == "budget" for row in report["cases"])
    assert all(row["budget"]["violation_code"] == "calls" for row in report["cases"])


def test_invalid_repeats_rejected_before_model_creation(monkeypatch):
    monkeypatch.setattr(comparison, "get_client", lambda: pytest.fail("Invalid repeats must be local"))
    with pytest.raises(ValueError, match="repeats"):
        comparison.run_comparison([case()], None, repeats=0)


def test_swallowed_retrieval_error_is_saved_without_raw_error(monkeypatch):
    def retrieve(plan, trace, *args, **kwargs):
        trace.append({"step": "retrieval", "status": "failed", "detail": {
            "error_type": "ConnectionError", "error": "private secret", "attempt": 1,
        }})
        return []

    monkeypatch.setattr(comparison.ask_service, "retrieve_documents_with_retry", retrieve)

    def execute(question, strategy):
        plan = SimpleNamespace(retrieval_query=question, retrieval_strategy="standard_retrieval", top_k=7)
        comparison.ask_service.retrieve_documents_with_retry(plan, [])
        return {"answer": "", "sources": []}

    report = comparison.run_comparison([case()], object(), run_strategy_fn=execute)
    assert all(row["retrieval_trace"][0]["error_type"] == "ConnectionError" for row in report["cases"])
    assert all("retrieval_error" in row["diagnosis_candidates"] for row in report["cases"])
    assert "private secret" not in json.dumps(report)
