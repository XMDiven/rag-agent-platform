from agent_app.scripts.comparison_analysis import analyze_row, summarize_comparison
from rag_app.scripts.evaluate_retrieval import RetrievalEvalCase


def test_failed_and_unmeasured_rows_remain_in_summary_denominator():
    rows = [
        {"strategy": "rag", "case_type": "single_source", "passed": True,
         "completed": True, "execution_duration_seconds": 1, "total_duration_seconds": 2,
         "first_model_event_seconds": 0.1, "final_answer_first_token_seconds": 0.3,
         "input_tokens": 10, "output_tokens": 2, "model_requests": 1, "tool_calls": 0,
         "source_coverage_pass": True},
        {"strategy": "rag", "case_type": "single_source", "passed": False,
         "completed": False, "execution_duration_seconds": 9, "total_duration_seconds": 9,
         "failure_stage": "budget", "model_requests": 2},
    ]
    result = summarize_comparison(rows)
    group = result["by_strategy"]["rag"]
    assert group["total"] == 2
    assert group["pass_rate"] == 0.5
    assert group["completion_rate"] == 0.5
    assert group["source_coverage_rate"] == 0.5
    assert group["execution_duration_seconds"] == {"n": 2, "p50": 1, "p95": 9}
    assert group["final_answer_first_token_seconds"]["n"] == 1
    assert group["totals"]["model_requests"] == 3
    assert group["failure_counts"] == {"budget": 1}
    assert result["by_case_type"]["single_source"]["rag"] == group


def test_diagnoses_are_candidates_and_source_filename_coverage_is_not_groundedness():
    case = RetrievalEvalCase("x", "question", ["a.md", "b.md"], "all")
    row = {"sources": [{"source": "a.md", "snippet": "text"}],
           "retrieval_trace": [{"question": "same"}, {"question": "same"}],
           "judge": {"groundedness_score": 2}, "failure_stage": None,
           "budget": {"request_trace": [{"finish_reason": "length"}]}}
    analyze_row(row, case)
    assert row["case_type"] == "multi_source"
    assert row["source_coverage"] == 0.5
    assert not row["source_coverage_pass"]
    assert set(row["diagnosis_candidates"]) == {
        "expected_source_missing", "repeated_retrieval_query", "low_groundedness", "output_truncated",
    }


def test_alternative_sources_are_not_multi_source_and_judge_costs_stay_separate():
    case = RetrievalEvalCase("x", "q", ["a.md", "b.md"], "any")
    row = {"strategy": "rag", "sources": [{"source": "a.md"}], "passed": True,
           "judge_requests": 1, "judge_usage": {"input_tokens": 12, "output_tokens": 3},
           "cost": None, "judge_cost": 0.02}
    analyze_row(row, case)
    assert row["case_type"] == "single_source"
    group = summarize_comparison([row])["by_strategy"]["rag"]
    assert group["judge_totals"]["input_tokens"] == 12
    assert group["judge_totals"]["output_tokens"] == 3
    assert group["totals"]["judge_requests"] == 1
    assert group["generation_cost"] is None
    assert group["judge_cost"] == 0.02
