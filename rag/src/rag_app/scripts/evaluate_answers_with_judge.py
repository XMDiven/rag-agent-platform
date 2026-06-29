from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from rag_app.config import config
from rag_app.evaluation.answer_judge import judge_answer
from rag_app.infrastructure.resources import AppResources, create_app_resources
from rag_app.retrieval.retriever import RetrievalSearchType, build_search_kwargs
from rag_app.scripts.evaluate_retrieval import RetrievalEvalCase, load_case
from rag_app.services.ask_service import ask_question


def build_custom_cases(
    cases: list[dict[str, str]],
) -> list[RetrievalEvalCase]:
    return [
        RetrievalEvalCase(
            id=case["id"],
            question=case["question"],
            expected_source_contains=[],
        )
        for case in cases
    ]


def build_error_detail(error: Exception) -> dict[str, str]:
    return {
        "error_type": type(error).__name__,
        "error": str(error),
    }


def normalize_sources(sources: Any) -> list[dict[str, Any]]:
    if isinstance(sources, list):
        return [
            source
            for source in sources
            if isinstance(source, dict)
        ]

    return []


def evaluate_case(
    case: RetrievalEvalCase,
    llm: BaseChatModel,
    resources: AppResources | None = None,
    top_k: int | None = None,
    search_type: RetrievalSearchType | None = None,
    fetch_k: int | None = None,
    lambda_mult: float | None = None,
) -> dict[str, Any]:
    case_start = perf_counter()
    answer_start = perf_counter()

    try:
        result = ask_question(
            case.question,
            resources=resources,
            top_k=top_k,
            search_type=search_type,
            fetch_k=fetch_k,
            lambda_mult=lambda_mult,
        )

    except Exception as error:
        answer_duration_seconds = round(perf_counter() - answer_start, 2)
        return {
            "id": case.id,
            "question": case.question,
            "answer": "",
            "sources": [],
            "judge": None,
            "passed": False,
            "failures": ["answer generation failed"],
            "answer_error": build_error_detail(error),
            "answer_duration_seconds": answer_duration_seconds,
            "judge_duration_seconds": 0.0,
            "total_duration_seconds": round(perf_counter() - case_start, 2),
        }

    answer_duration_seconds = round(perf_counter() - answer_start, 2)
    answer = str(result.get("answer", ""))
    sources = normalize_sources(result.get("sources", []))

    judge_start = perf_counter()

    try:
        judge_result = judge_answer(
            question=case.question,
            answer=answer,
            sources=sources,
            llm=llm,
        )
    except Exception as error:
        judge_duration_seconds = round(perf_counter() - judge_start, 2)
        return {
            "id": case.id,
            "question": case.question,
            "answer": answer,
            "sources": sources,
            "judge": None,
            "passed": False,
            "failures": ["judge failed"],
            "judge_error": build_error_detail(error),
            "answer_duration_seconds": answer_duration_seconds,
            "judge_duration_seconds": judge_duration_seconds,
            "total_duration_seconds": round(perf_counter() - case_start, 2),
        }

    judge_duration_seconds = round(perf_counter() - judge_start, 2)

    return {
        "id": case.id,
        "question": case.question,
        "answer": answer,
        "sources": sources,
        "judge": judge_result.model_dump(),
        "passed": judge_result.overall_pass,
        "failures": [],
        "answer_duration_seconds": answer_duration_seconds,
        "judge_duration_seconds": judge_duration_seconds,
        "total_duration_seconds": round(perf_counter() - case_start, 2),
    }


def save_report(
    report: dict[str, Any],
    output_dir: Path | None = None,
) -> Path:
    if output_dir is None:
        output_dir = config.PROJECT_ROOT / "experiments" / "runs" / "judge"

    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{report['run_id']}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"judge report saved to: {output_path}")

    return output_path


def run_evaluation(
    prompt_version: str | None = None,
    case_limit: int | None = None,
    cases: list[dict[str, str]] | None = None,
    top_k: int | None = None,
    search_type: RetrievalSearchType | None = None,
    fetch_k: int | None = None,
    lambda_mult: float | None = None,
) -> dict[str, Any]:
    previous_prompt_version = config.QA_PROMPT_VERSION
    effective_search_type, search_kwargs = build_search_kwargs(
        top_k=top_k,
        search_type=search_type,
        fetch_k=fetch_k,
        lambda_mult=lambda_mult,
    )

    if prompt_version is not None:
        config.QA_PROMPT_VERSION = prompt_version

    try:
        eval_cases = build_custom_cases(cases) if cases is not None else load_case()
        case_source = "custom" if cases is not None else "default"

        if case_limit is not None:
            eval_cases = eval_cases[:case_limit]

        resources = create_app_resources()
        llm = resources.llm_client
        results = []

        for index, case in enumerate(eval_cases, start=1):
            print(
                f"Evaluating judge case {index}/{len(eval_cases)}: {case.id}",
                flush=True,
            )
            if (
                top_k is None
                and search_type is None
                and fetch_k is None
                and lambda_mult is None
            ):
                result = evaluate_case(
                    case,
                    llm,
                    resources=resources,
                )
            else:
                result = evaluate_case(
                    case,
                    llm,
                    resources=resources,
                    top_k=top_k,
                    search_type=search_type,
                    fetch_k=fetch_k,
                    lambda_mult=lambda_mult,
                )
            results.append(result)
            print(
                (
                    f"completed {case.id} passed={result['passed']} "
                    f"total={result['total_duration_seconds']}s "
                    f"answer={result['answer_duration_seconds']}s "
                    f"judge={result['judge_duration_seconds']}s"
                ),
                flush=True,
            )

        passed_count = sum(result["passed"] for result in results)
        total_count = len(results)

        return {
            "run_id": datetime.now().strftime("%Y%m%d-%H%M%S"),
            "prompt_version": config.QA_PROMPT_VERSION,
            "total": total_count,
            "passed": passed_count,
            "failed": total_count - passed_count,
            "cases": results,
            "case_source": case_source,
            "retrieval_config": {
                "top_k": search_kwargs["k"],
                "search_type": effective_search_type,
                "fetch_k": search_kwargs.get("fetch_k"),
                "lambda_mult": search_kwargs.get("lambda_mult"),
            },
        }
    finally:
        config.QA_PROMPT_VERSION = previous_prompt_version


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate generated RAG answers with an LLM judge.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Number of documents to retrieve.",
    )
    parser.add_argument(
        "--search-type",
        choices=["similarity", "mmr"],
        default=None,
        help="Retriever search strategy. Defaults to RETRIEVAL_SEARCH_TYPE.",
    )
    parser.add_argument(
        "--fetch-k",
        type=int,
        default=None,
        help="MMR candidate pool size.",
    )
    parser.add_argument(
        "--lambda-mult",
        type=float,
        default=None,
        help="MMR relevance/diversity balance.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_evaluation(
        top_k=args.top_k,
        search_type=args.search_type,
        fetch_k=args.fetch_k,
        lambda_mult=args.lambda_mult,
    )
    save_report(report)


if __name__ == "__main__":
    main()
