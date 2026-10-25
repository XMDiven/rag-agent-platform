"""Descriptive statistics and trace-based review candidates for A2 experiments."""

from collections import Counter
from typing import Any

from agent_app.scripts.evaluate_agent import nearest_rank_percentile
from rag_app.scripts.evaluate_retrieval import RetrievalEvalCase, count_expected_source_hits, has_expected_sources


def analyze_row(row: dict[str, Any], case: RetrievalEvalCase) -> None:
    row["case_type"] = "multi_source" if case.match == "all" and len(case.expected_source_contains) > 1 else "single_source"
    sources = [str(source.get("source", "")) for source in row["sources"]]
    row["source_coverage"] = (
        count_expected_source_hits(sources, case.expected_source_contains) / len(case.expected_source_contains)
        if case.expected_source_contains else None
    )
    row["source_coverage_pass"] = has_expected_sources(sources, case.expected_source_contains, case.match)
    candidates = []
    if not row["source_coverage_pass"]:
        candidates.append("expected_source_missing")
    queries = [entry["question"] for entry in row.get("retrieval_trace", [])]
    if len(queries) > len(set(queries)):
        candidates.append("repeated_retrieval_query")
    if any(entry.get("error_type") for entry in row.get("retrieval_trace", [])):
        candidates.append("retrieval_error")
    if (row.get("judge") or {}).get("groundedness_score", 5) < 4:
        candidates.append("low_groundedness")
    if any(entry.get("finish_reason") == "length" for entry in row.get("budget", {}).get("request_trace", [])):
        candidates.append("output_truncated")
    if row.get("failure_stage"):
        candidates.append(row["failure_stage"])
    row["diagnosis_candidates"] = candidates


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    result = {"total": total}
    for field, label in (("passed", "pass_rate"), ("completed", "completion_rate"), ("source_coverage_pass", "source_coverage_rate")):
        result[label] = sum(bool(row.get(field)) for row in rows) / total
    for field in ("execution_duration_seconds", "total_duration_seconds", "first_model_event_seconds", "final_answer_first_token_seconds"):
        values = [row[field] for row in rows if row.get(field) is not None]
        result[field] = {
            "n": len(values),
            "p50": round(nearest_rank_percentile(values, 0.5), 3) if values else None,
            "p95": round(nearest_rank_percentile(values, 0.95), 3) if values else None,
        }
    result["totals"] = {
        field: sum(row.get(field, 0) for row in rows)
        for field in ("model_requests", "judge_requests", "llm_calls", "input_tokens", "cached_input_tokens", "output_tokens", "tool_calls", "rag_calls", "retrieval_calls")
    }
    result["judge_totals"] = {
        field: sum((row.get("judge_usage") or {}).get(field, 0) for row in rows)
        for field in ("llm_calls", "input_tokens", "cached_input_tokens", "output_tokens")
    }
    for field, label in (("cost", "generation_cost"), ("judge_cost", "judge_cost")):
        values = [row.get(field) for row in rows]
        result[label] = round(sum(values), 6) if all(value is not None for value in values) else None
    result["failure_counts"] = dict(Counter(
        row.get("failure_stage") or "judge_failed" for row in rows if not row.get("passed")
    ))
    return result


def summarize_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "by_strategy": {
            strategy: _summarize([row for row in rows if row["strategy"] == strategy])
            for strategy in dict.fromkeys(row["strategy"] for row in rows)
        },
        "by_case_type": {
            case_type: {
                strategy: _summarize([row for row in rows if row["case_type"] == case_type and row["strategy"] == strategy])
                for strategy in dict.fromkeys(row["strategy"] for row in rows if row["case_type"] == case_type)
            }
            for case_type in dict.fromkeys(row["case_type"] for row in rows)
        },
    }
