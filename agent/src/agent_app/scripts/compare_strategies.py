"""Compare existing RAG, fixed workflow and Agent on the same retrieval cases."""

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import asdict
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel

from agent_app.orchestration.executor import run_decomposed_retrieval
from agent_app.prompts import AGENT_LOOP_SYSTEM_PROMPT
from agent_app.scripts.evaluate_agent import AGENT_PROJECT_ROOT, extract_tool_names
from agent_app.scripts.comparison_analysis import analyze_row, summarize_comparison
from agent_app.scripts.comparison_budget import (
    BudgetExceeded, BudgetLimits, JUDGE_LIMITS, ModelBudget, SharedQuota,
    comparison_clients, final_answer_stage,
)
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
    with final_answer_stage():
        answer = generate_answer(
            question=question, context=format_evidence(sources),
            llm=get_client(), prompt=get_qa_prompt(),
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
        "sdk_retries": 0,
        "generation_retries": 0,
        "retrieval_retries": 0,
        "sampling": "provider default; temperature and seed omitted",
        "input_price_per_million": config.LLM_INPUT_PRICE_PER_MILLION,
        "cached_input_price_per_million": config.LLM_CACHED_INPUT_PRICE_PER_MILLION,
        "output_price_per_million": config.LLM_OUTPUT_PRICE_PER_MILLION,
        "answer_cache": False,
    }


def _cost(usage: RequestUsage, scope: ModelBudget, phase: str) -> float | None:
    prices = (config.LLM_INPUT_PRICE_PER_MILLION, config.LLM_CACHED_INPUT_PRICE_PER_MILLION, config.LLM_OUTPUT_PRICE_PER_MILLION)
    requests = [request for request in scope.request_trace if request["phase"] == phase]
    complete_usage = usage.llm_calls == len(requests) and all(
        isinstance((request.get("usage") or {}).get(field), int)
        and not isinstance(request["usage"][field], bool) and request["usage"][field] >= 0
        for request in requests for field in ("prompt_tokens", "completion_tokens")
    )
    return usage.cost() if all(price > 0 for price in prices) and complete_usage else None


def input_manifest(cases: list[RetrievalEvalCase]) -> dict[str, Any]:
    root = AGENT_PROJECT_ROOT.parent
    files = sorted(config.RAW_DATA_DIR.glob("*.md")) + sorted(config.RAW_DATA_DIR.glob("*.pdf"))
    code_files = sorted((root / "rag" / "src").rglob("*.py")) + sorted((root / "agent" / "src").rglob("*.py"))
    code = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, text=True, capture_output=True, check=True).stdout.strip()
    return {
        "cases_sha256": hashlib.sha256(json.dumps([asdict(case) for case in cases], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "source_hashes": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
        "code_hashes": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in code_files},
        "qa_prompt_sha256": hashlib.sha256(str(get_qa_prompt()).encode()).hexdigest(),
        "code_commit": code, "tracked_changes_present": bool(dirty),
        "index_freeze": "Not asserted by this file manifest; record collection/payload snapshots separately.",
    }


def run_comparison(
    cases: list[RetrievalEvalCase], judge_llm: BaseChatModel | None = None, *,
    run_strategy_fn: Callable[[str, str], dict[str, Any]] = run_strategy,
    judge_fn: Callable[..., AnswerJudgeResult] = judge_answer,
    timer: Callable[[], float] = perf_counter, now: datetime | None = None,
    repeats: int = 1, quota: SharedQuota | None = None,
    limits: BudgetLimits | None = None, transport_factory=None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("cases must be nonempty with unique ids")
    quota = quota if quota is not None else SharedQuota()
    rows = []
    original_ask = ask_service.ask_question
    original_retrieve = ask_service.retrieve_documents_with_retry
    with comparison_clients(transport_factory), patch.object(ask_service, "get_answer_cache", return_value=None):
        shared_configuration = configuration()
        for case in cases:
            for repeat in range(repeats):
                # Rotate order so the same strategy is not always first on warm resources.
                order = STRATEGIES[repeat % len(STRATEGIES):] + STRATEGIES[:repeat % len(STRATEGIES)]
                for strategy in order:
                    print(f"Comparing {case.id} repeat {repeat + 1}: {strategy}", file=sys.stderr, flush=True)
                    scope = ModelBudget(strategy, limits=limits, quota=quota)
                    retrieval_trace = []
                    rag_calls = 0

                    def counted_ask(*args, **kwargs):
                        nonlocal rag_calls
                        scope.check_work()
                        rag_calls += 1
                        return original_ask(*args, **kwargs)

                    def counted_retrieve(retrieval_plan, trace, *args, **kwargs):
                        scope.check_work()
                        started = timer()
                        entry = {"question": retrieval_plan.retrieval_query,
                                 "strategy": retrieval_plan.retrieval_strategy, "top_k": retrieval_plan.top_k}
                        retrieval_trace.append(entry)
                        trace_start = len(trace)
                        try:
                            documents = original_retrieve(retrieval_plan, trace, *args, **kwargs)
                            entry["sources"] = ask_service.build_sources(documents)
                            entry["document_count"] = len(documents)
                            scope.check()
                            return documents
                        except Exception as exc:
                            entry["error_type"] = type(exc).__name__
                            raise
                        finally:
                            entry["attempts"] = [{
                                "status": item.get("status"),
                                **{key: value for key, value in item.get("detail", {}).items()
                                   if key in ("attempt", "error_type", "duration_seconds", "document_count")},
                            } for item in trace[trace_start:] if item.get("step") == "retrieval"]
                            errors = [item["error_type"] for item in entry["attempts"] if item.get("error_type")]
                            if errors:
                                entry["error_type"] = errors[-1]
                            entry["duration_seconds"] = round(timer() - started, 3)

                    row = {
                        "id": case.id, "question": case.question, "strategy": strategy, "repeat": repeat + 1,
                        "answer": "", "sources": [], "tools": [], "tool_calls": 0,
                        "termination_reason": "error", "passed": False, "completed": False,
                        "failure_stage": None, "error": None,
                        "judge": None, "judge_evidence": None, "judge_usage": None,
                        "judge_duration_seconds": 0.0, "judge_cost": None,
                    }
                    with scope:
                        usage = start_request_usage()
                        started = timer()
                        try:
                            scope.check_work()
                            with patch.object(ask_service, "ask_question", counted_ask), patch.object(
                                retrieval_tool, "ask_question", counted_ask,
                            ), patch.object(ask_service, "retrieve_documents_with_retry", counted_retrieve):
                                output = run_strategy_fn(case.question, strategy)
                            row["answer"] = output.get("answer", "")
                            row["sources"] = [s for s in output.get("sources", []) if isinstance(s, dict)]
                            row["tools"] = output.get("tools", [])
                            row["tool_calls"] = len(row["tools"])
                            row["termination_reason"] = output.get("termination_reason", "final_answer")
                            if "sub_questions" in output:
                                row["sub_questions"] = output["sub_questions"]
                            scope.check()  # Legacy fallback must not swallow a budget violation.
                            if not output.get("execution_succeeded", True) or row["termination_reason"] != "final_answer":
                                row["failure_stage"] = "failed_termination"
                            elif not row["answer"].strip() or row["answer"] == config.FALLBACK_ANSWER:
                                row["failure_stage"] = "empty_answer"
                            elif not row["sources"]:
                                row["failure_stage"] = "missing_sources"
                            elif any(request.get("finish_reason") == "length" for request in scope.request_trace):
                                row["failure_stage"] = "output_truncated"
                        except Exception as exc:
                            row["failure_stage"] = "budget" if isinstance(exc, BudgetExceeded) or scope.violation_code else "execution"
                            row["error"] = {"type": type(exc).__name__}
                        row["execution_duration_seconds"] = round(timer() - started, 3)
                        row.update(usage_values(usage))
                        row["cost"] = _cost(usage, scope, "generation")
                        row["rag_calls"] = rag_calls
                        row["retrieval_calls"] = len(retrieval_trace)
                        row["retrieval_trace"] = retrieval_trace
                        row["completed"] = row["failure_stage"] is None
                        if row["completed"]:
                            judge_usage = start_request_usage()
                            started = timer()
                            try:
                                scope.set_phase("judge")
                                row["judge_evidence"] = format_evidence(row["sources"])
                                judged = judge_fn(
                                    question=case.question, answer=row["answer"], sources=row["sources"],
                                    llm=judge_llm if judge_llm is not None else get_client(),
                                )
                                scope.check()
                                row["judge"] = judged.model_dump()
                                row["passed"] = judged.overall_pass
                            except Exception as exc:
                                row["failure_stage"] = "judge_budget" if isinstance(exc, BudgetExceeded) or scope.violation_code else "judge"
                                row["error"] = {"type": type(exc).__name__}
                            row["judge_duration_seconds"] = round(timer() - started, 3)
                            row["judge_usage"] = usage_values(judge_usage)
                            row["judge_cost"] = _cost(judge_usage, scope, "judge")
                    row["budget"] = scope.to_dict()
                    row["model_requests"] = row["budget"]["generation"]["calls"]
                    row["judge_requests"] = row["budget"]["judge"]["calls"]
                    for field in ("first_model_event_seconds", "final_answer_first_token_seconds"):
                        row[field] = row["budget"][field]
                    row["total_duration_seconds"] = round(row["execution_duration_seconds"] + row["judge_duration_seconds"], 3)
                    analyze_row(row, case)
                    rows.append(row)
                    if checkpoint:
                        checkpoint({"cases": rows, "quota": quota.to_dict()})
    passed = sum(row["passed"] for row in rows)
    return {
        "schema_version": 2,
        "run_id": (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S-%f"),
        "configuration": shared_configuration, "manifest": input_manifest(cases),
        "limits": asdict(limits or BudgetLimits()), "judge_limits": asdict(JUDGE_LIMITS),
        "quota": quota.to_dict(), "repeats": repeats,
        "timing_scope": "in-process model callbacks; excludes HTTP/browser transport",
        "strategies": list(STRATEGIES),
        "summary": {"total": len(rows), "passed": passed, "failed": len(rows) - passed},
        "statistics": summarize_comparison(rows), "cases": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--only", default=DEFAULT_CASE_ID, help="Comma-separated existing case IDs.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--quota-file", type=Path, help="Serial phases share this local quota/checkpoint JSON.")
    parser.add_argument("--run", action="store_true", help="Run models after budget approval; default only previews.")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        raise ValueError("repeats must be positive")
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
            "repeats": args.repeats, "limits": asdict(BudgetLimits()),
            "judge_limits": asdict(JUDGE_LIMITS), "shared_quota": SharedQuota().to_dict(),
            "configuration": configuration(),
            "cases": [{
                "id": case.id, "question": case.question,
                "fixed_sub_questions": run_question_decompose_tool(case.question)["sub_questions"],
            } for case in cases],
        }, ensure_ascii=False, indent=2))
        return 0
    quota = SharedQuota()
    if args.quota_file and args.quota_file.exists():
        quota = SharedQuota(**json.loads(args.quota_file.read_text())["quota"])

    def checkpoint(state):
        if args.quota_file:
            args.quota_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.quota_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
            temporary.replace(args.quota_file)

    report = run_comparison(cases, repeats=args.repeats, quota=quota, checkpoint=checkpoint)
    report["dataset"] = str(args.cases)
    path = write_report(report, output_dir=args.output_dir)
    print(f"Comparison report: {path}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
