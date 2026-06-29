from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from rag_app.config import config
from rag_app.retrieval.retriever import (
    RetrievalSearchType,
    build_search_kwargs,
    get_retriever,
)

DEFAULT_CASES_PATH = (
    config.PROJECT_ROOT / "experiments" / "datasets" / "retrieval_eval_cases.json"
)


@dataclass(frozen=True)
class RetrievalEvalCase:
    id: str
    question: str
    expected_source_contains: list[str]
    match: str = "any"


def load_case(path: Path = DEFAULT_CASES_PATH) -> list[RetrievalEvalCase]:
    raw_cases = json.loads(path.read_text(encoding="utf-8"))

    return [
        RetrievalEvalCase(
            id=case["id"],
            question=case["question"],
            expected_source_contains=case["expected_source_contains"],
            match=case.get("match", "any"),
        )
        for case in raw_cases
    ]


def get_sources(documents: list[Document]) -> list[str]:
    return [
        document.metadata.get("source", "")
        for document in documents
    ]


def find_first_hit_rank(
    sources: list[str],
    expected_source_contains: list[str],
) -> int | None:
    normalize_sources = [source.lower() for source in sources]
    normalize_expected = [expected.lower() for expected in expected_source_contains]

    for index, source in enumerate(normalize_sources, start=1):
        if any(expected in source for expected in normalize_expected):
            return index

    return None


def count_expected_source_hits(
    sources: list[str],
    expected_source_contains: list[str],
) -> int:
    normalize_sources = [source.lower() for source in sources]
    normalize_expected = [expected.lower() for expected in expected_source_contains]

    return sum(
        1
        for expected in normalize_expected
        if any(expected in source for source in normalize_sources)
    )


def has_expected_sources(
    sources: list[str],
    expected_source_contains: list[str],
    match: str = "any",
) -> bool:
    normalize_sources = [source.lower() for source in sources]
    normalize_expected = [expected.lower() for expected in expected_source_contains]

    if match == "any":
        return any(
            expected in source
            for source in normalize_sources
            for expected in normalize_expected
        )

    if match == "all":
        return all(
            any(expected in source for source in normalize_sources)
            for expected in normalize_expected
        )

    raise ValueError(f"Unsupported match mode: {match}")


def evaluate_source_hits(
    sources: list[str],
    expected_source_contains: list[str],
    match: str = "any",
) -> dict[str, Any]:
    first_hit_rank = find_first_hit_rank(
        sources=sources,
        expected_source_contains=expected_source_contains,
    )
    expected_total = len(expected_source_contains)
    expected_hit_count = count_expected_source_hits(
        sources=sources,
        expected_source_contains=expected_source_contains,
    )
    passed = has_expected_sources(
        sources=sources,
        expected_source_contains=expected_source_contains,
        match=match,
    )

    reciprocal_rank = 0.0
    if first_hit_rank is not None:
        reciprocal_rank = round(1 / first_hit_rank, 3)

    source_coverage = 0.0
    if expected_total > 0:
        source_coverage = round(expected_hit_count / expected_total, 3)

    return {
        "passed": passed,
        "first_hit_rank": first_hit_rank,
        "reciprocal_rank": reciprocal_rank,
        "expected_source_hit_count": expected_hit_count,
        "expected_source_total": expected_total,
        "expected_source_coverage": source_coverage,
    }


def evaluate_case(
    case: RetrievalEvalCase,
    documents: list[Document],
) -> dict[str, Any]:
    sources = get_sources(documents)

    source_metrics = evaluate_source_hits(
        sources=sources,
        expected_source_contains=case.expected_source_contains,
        match=case.match,
    )

    passed = source_metrics["passed"]
    status = "PASS" if passed else "FAIL"

    print(f"Evaluating {case.id} with {status}")
    print(f"question: {case.question}")
    print(f"expected_source_contains: {case.expected_source_contains}")
    print("retrieved sources:")

    for source in sources:
        print(f"- {source}")

    print()

    return {
        "id": case.id,
        "question": case.question,
        "passed": passed,
        "expected_source_contains": case.expected_source_contains,
        "retrieved_sources": sources,
        "unique_source_count": len(set(sources)),
        "duplicate_source_count": len(sources) - len(set(sources)),
        "source_metrics": source_metrics,
    }


def run_evaluation(
    top_k: int | None = None,
    search_type: RetrievalSearchType | None = None,
    fetch_k: int | None = None,
    lambda_mult: float | None = None,
    cases_path: Path | None = None,
) -> dict[str, Any]:
    cases = load_case(cases_path) if cases_path is not None else load_case()
    effective_search_type, search_kwargs = build_search_kwargs(
        top_k=top_k,
        search_type=search_type,
        fetch_k=fetch_k,
        lambda_mult=lambda_mult,
    )
    retriever = get_retriever(
        top_k=top_k,
        search_type=search_type,
        fetch_k=fetch_k,
        lambda_mult=lambda_mult,
    )
    case_results = []

    for case in cases:
        documents = retriever.invoke(case.question)
        result = evaluate_case(case, documents)
        case_results.append(result)

    passed_count = sum(result["passed"] for result in case_results)
    total_count = len(case_results)
    source_metrics = [result["source_metrics"] for result in case_results]

    source_hit_rate = (
        round(passed_count / total_count, 3)
        if total_count
        else 0.0
    )
    mrr = (
        round(
            sum(metric["reciprocal_rank"] for metric in source_metrics)
            / total_count,
            3,
        )
        if total_count
        else 0.0
    )
    average_expected_source_coverage = (
        round(
            sum(
                metric["expected_source_coverage"]
                for metric in source_metrics
            )
            / total_count,
            3,
        )
        if total_count
        else 0.0
    )
    average_unique_source_count = (
        round(
            sum(result["unique_source_count"] for result in case_results)
            / total_count,
            3,
        )
        if total_count
        else 0.0
    )
    average_duplicate_source_count = (
        round(
            sum(result["duplicate_source_count"] for result in case_results)
            / total_count,
            3,
        )
        if total_count
        else 0.0
    )

    print(
        "retrieval_config: "
        f"search_type={effective_search_type} top_k={search_kwargs['k']} "
        f"fetch_k={search_kwargs.get('fetch_k')} "
        f"lambda_mult={search_kwargs.get('lambda_mult')}"
    )
    print(f"summary: {passed_count}/{total_count} passed")
    print(f"source_hit_rate: {source_hit_rate:.3f}")
    print(f"mrr: {mrr:.3f}")
    print(
        "average_expected_source_coverage: "
        f"{average_expected_source_coverage:.3f}"
    )
    print(f"average_unique_source_count: {average_unique_source_count:.3f}")
    print(
        "average_duplicate_source_count: "
        f"{average_duplicate_source_count:.3f}"
    )

    return {
        "passed": passed_count,
        "total": total_count,
        "retrieval_config": {
            "top_k": search_kwargs["k"],
            "search_type": effective_search_type,
            "fetch_k": search_kwargs.get("fetch_k"),
            "lambda_mult": search_kwargs.get("lambda_mult"),
        },
        "source_hit_rate": source_hit_rate,
        "mrr": mrr,
        "average_expected_source_coverage": average_expected_source_coverage,
        "average_unique_source_count": average_unique_source_count,
        "average_duplicate_source_count": average_duplicate_source_count,
        "cases": case_results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate retrieval source-hit and ranking metrics.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Number of documents to return. Defaults to RETRIEVAL_TOP_K.",
    )
    parser.add_argument(
        "--search-type",
        choices=["similarity", "mmr" , "hybrid"],
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
    parser.add_argument(
        "--cases-path",
        type=str,
        default=None,
        help="Path to an eval cases JSON. Defaults to the standard golden set.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_evaluation(
        top_k=args.top_k,
        search_type=args.search_type,
        fetch_k=args.fetch_k,
        lambda_mult=args.lambda_mult,
        cases_path=Path(args.cases_path) if args.cases_path else None,
    )

    if summary["passed"] != summary["total"]:
        raise SystemExit(1)


if "__main__" == __name__:
    main()
