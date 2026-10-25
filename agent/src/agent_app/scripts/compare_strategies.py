"""Compare existing RAG, fixed workflow and Agent on the same retrieval cases."""

import argparse
import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel

from agent_app.orchestration.executor import run_decomposed_retrieval
from agent_app.prompts import AGENT_LOOP_SYSTEM_PROMPT
from agent_app.scripts.evaluate_agent import AGENT_PROJECT_ROOT, extract_tool_names
from agent_app.scripts.evaluate_agent_answers import write_report
from agent_app.service import run_agent
from agent_app.tools import retrieval as retrieval_tool
from agent_app.tools.question_decompose import run_question_decompose_tool
from rag_app.config import config
from rag_app.evaluation.answer_judge import format_evidence, judge_answer
from rag_app.evaluation.judge_schema import AnswerJudgeResult
from rag_app.generation.answer_generator import generate_answer
from rag_app.generation.qa_prompt import get_qa_prompt
from rag_app.infrastructure.llm_client import get_client
from rag_app.infrastructure.token_usage import RequestUsage, start_request_usage
from rag_app.scripts.evaluate_retrieval import DEFAULT_CASES_PATH, RetrievalEvalCase, load_case
from rag_app.services import ask_service


STRATEGIES = ("rag", "fixed", "agent")
DEFAULT_CASE_ID = "langchain_llamaindex_comparison"
DEFAULT_OUTPUT_DIR = AGENT_PROJECT_ROOT / "experiments" / "runs" / "comparison"


def run_fixed_workflow(question: str) -> dict[str, Any]:
    result = run_decomposed_retrieval(question)
    sub_results = result.get("sub_results", [])
    if not sub_results or any(part.get("status") != "success" for part in sub_results):
        raise RuntimeError("fixed_workflow_subquestion_failed")
    sources = result.get("sources", [])
    if not sources:
        raise RuntimeError("fixed_workflow_missing_evidence")
    if any(
        not part.get("sources")
        or not part.get("answer", "").strip()
        or part["answer"] == config.FALLBACK_ANSWER
        for part in sub_results
    ):
        raise RuntimeError("fixed_workflow_incomplete_subquestion")
    answer = generate_answer(
        question=question,
        context=format_evidence(sources),
        llm=get_client(),
        prompt=get_qa_prompt(),
    )
    return {"answer": answer, "sources": sources, "sub_questions": result["sub_questions"]}


def run_strategy(question: str, strategy: str) -> dict[str, Any]:
    if strategy == "agent":
        result = run_agent(question)
        return {
            **result.tool_result.output,
            "tools": extract_tool_names(result),
            "termination_reason": result.termination_reason,
            "execution_succeeded": result.tool_result.status == "success",
        }
    if strategy == "rag":
        output = ask_service.ask_question(question)
    elif strategy == "fixed":
        output = run_fixed_workflow(question)
    else:
        raise ValueError(f"unknown strategy: {strategy}")
    return {**output, "tools": [], "termination_reason": "final_answer"}


def usage_values(usage: RequestUsage) -> dict[str, int]:
    return {
        name: getattr(usage, name)
        for name in ("llm_calls", "input_tokens", "cached_input_tokens", "output_tokens")
    }


def configuration() -> dict[str, Any]:
    return {
        "model": config.settings.llm_model_id,
        "thinking_type": config.settings.llm_thinking_type,
        "judge_independence": "same_model",
        "embedding_model": config.settings.embedding_model,
        "collection": config.COLLECTION_NAME,
        "retrieval_search_type": config.RETRIEVAL_SEARCH_TYPE,
        "retrieval_top_k": config.RETRIEVAL_TOP_K,
        "retrieval_fetch_k": config.RETRIEVAL_FETCH_K,
        "retrieval_lambda_mult": config.RETRIEVAL_LAMBDA_MULT,
        "hybrid_candidate_k": config.RETRIEVAL_HYBRID_CANDIDATE_K,
        "hybrid_bm25_weight": config.RETRIEVAL_HYBRID_BM25_WEIGHT,
        "hybrid_dense_weight": config.RETRIEVAL_HYBRID_DENSE_WEIGHT,
        "chunk_size": config.CHUNK_SIZE,
        "chunk_overlap": config.CHUNK_OVERLAP,
        "qa_prompt_version": config.QA_PROMPT_VERSION,
        "agent_system_prompt": AGENT_LOOP_SYSTEM_PROMPT,
        "sdk_retries": config.LLM_MAX_RETRIES,
        "generation_retries": config.MAX_GENERATION_RETRY,
        "retrieval_retries": config.MAX_RETRIEVAL_RETRY,
        "answer_cache": False,
    }


def run_comparison(
    cases: list[RetrievalEvalCase],
    judge_llm: BaseChatModel,
    *,
    run_strategy_fn: Callable[[str, str], dict[str, Any]] = run_strategy,
    judge_fn: Callable[..., AnswerJudgeResult] = judge_answer,
    timer: Callable[[], float] = perf_counter,
    now: datetime | None = None,
) -> dict[str, Any]:
    rows = []
    original_ask = ask_service.ask_question
    # Cover direct RAG and the function imported by the Agent's retrieval tool.
    # A cached getter may already hold a Redis client, so changing REDIS_URL alone
    # would not reliably disable the answer cache. Restore all aliases on exit.
    with patch.object(ask_service, "get_answer_cache", return_value=None):
        for case in cases:
            for strategy in STRATEGIES:
                print(f"Comparing {case.id}: {strategy}", file=sys.stderr, flush=True)
                retrieval_calls = 0

                def counted_ask(*args, **kwargs):
                    nonlocal retrieval_calls
                    retrieval_calls += 1
                    return original_ask(*args, **kwargs)

                row = {
                    "id": case.id, "question": case.question, "strategy": strategy,
                    "answer": "", "sources": [], "tools": [], "tool_calls": 0,
                    "termination_reason": "error", "passed": False,
                    "failure_stage": None, "error": None,
                    "judge": None, "judge_evidence": None, "judge_usage": None,
                    "judge_duration_seconds": 0.0,
                }
                usage = start_request_usage()
                started = timer()
                try:
                    with patch.object(ask_service, "ask_question", counted_ask), patch.object(
                        retrieval_tool, "ask_question", counted_ask,
                    ):
                        output = run_strategy_fn(case.question, strategy)
                    row["answer"] = output.get("answer", "")
                    row["sources"] = [s for s in output.get("sources", []) if isinstance(s, dict)]
                    row["tools"] = output.get("tools", [])
                    row["tool_calls"] = len(row["tools"])
                    row["termination_reason"] = output.get("termination_reason", "final_answer")
                    if "sub_questions" in output:
                        row["sub_questions"] = output["sub_questions"]
                    if not output.get("execution_succeeded", True) or row["termination_reason"] != "final_answer":
                        row["failure_stage"] = "failed_termination"
                    elif not row["answer"].strip() or row["answer"] == config.FALLBACK_ANSWER:
                        row["failure_stage"] = "empty_answer"
                    elif not row["sources"]:
                        row["failure_stage"] = "missing_sources"
                except Exception as exc:
                    row["failure_stage"] = "execution"
                    row["error"] = {"type": type(exc).__name__}
                row["execution_duration_seconds"] = round(timer() - started, 3)
                row.update(usage_values(usage))
                row["retrieval_calls"] = retrieval_calls

                if row["failure_stage"] is None:
                    judge_usage = start_request_usage()
                    started = timer()
                    try:
                        row["judge_evidence"] = format_evidence(row["sources"])
                        judged = judge_fn(
                            question=case.question, answer=row["answer"],
                            sources=row["sources"], llm=judge_llm,
                        )
                        row["judge"] = judged.model_dump()
                        row["passed"] = judged.overall_pass
                    except Exception as exc:
                        row["failure_stage"] = "judge"
                        row["error"] = {"type": type(exc).__name__}
                    row["judge_duration_seconds"] = round(timer() - started, 3)
                    row["judge_usage"] = usage_values(judge_usage)
                row["total_duration_seconds"] = round(
                    row["execution_duration_seconds"] + row["judge_duration_seconds"], 3,
                )
                rows.append(row)
    passed = sum(row["passed"] for row in rows)
    return {
        "run_id": (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S-%f"),
        "configuration": configuration(),
        "strategies": list(STRATEGIES),
        "summary": {"total": len(rows), "passed": passed, "failed": len(rows) - passed},
        "cases": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--only", default=DEFAULT_CASE_ID, help="Comma-separated existing case IDs.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run", action="store_true", help="Run models after budget approval; default only previews.")
    args = parser.parse_args(argv)
    by_id = {case.id: case for case in load_case(args.cases)}
    selected = list(dict.fromkeys(value.strip() for value in args.only.split(",") if value.strip()))
    if not selected:
        raise ValueError("select at least one existing case with --only")
    unknown = set(selected) - by_id.keys()
    if unknown:
        raise ValueError(f"unknown case ids: {sorted(unknown)}")
    cases = [by_id[value] for value in selected]
    if not args.run:
        print(json.dumps({
            "mode": "preview", "strategies": list(STRATEGIES),
            "configuration": configuration(),
            "cases": [{
                "id": case.id, "question": case.question,
                "fixed_sub_questions": run_question_decompose_tool(case.question)["sub_questions"],
            } for case in cases],
        }, ensure_ascii=False, indent=2))
        return 0
    report = run_comparison(cases, judge_llm=get_client())
    report["dataset"] = str(args.cases)
    path = write_report(report, output_dir=args.output_dir)
    print(f"Comparison report: {path}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
