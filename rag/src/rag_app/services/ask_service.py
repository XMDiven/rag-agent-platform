import logging

from collections.abc import Iterator
from time import perf_counter
from typing import Any

from langchain_core.documents import Document

from rag_app.config import config
from rag_app.generation.answer_generator import generate_answer, stream_answer
from rag_app.generation.context_formatter import format_context
from rag_app.generation.qa_prompt import get_qa_prompt
from rag_app.infrastructure.answer_cache import (
    RedisAnswerCache,
    get_answer_cache,
)
from rag_app.infrastructure.llm_client import get_client
from rag_app.infrastructure.resources import AppResources
from rag_app.retrieval.query_analyzer import analyze_query
from rag_app.retrieval.retriever import RetrievalSearchType, get_retriever
from rag_app.retrieval.retrieval_planner import RetrievalPlan, plan_retrieval

logger = logging.getLogger(__name__)


def interleave_documents_by_source(documents: list[Document]) -> list[Document]:
    documents_by_source: dict[str, list[Document]] = {}
    source_order: list[str] = []

    for document in documents:
        source = str(document.metadata.get("source", "unknown"))

        if source not in documents_by_source:
            documents_by_source[source] = []
            source_order.append(source)

        documents_by_source[source].append(document)

    reordered_documents: list[Document] = []

    while len(reordered_documents) < len(documents):
        added_document = False

        for source in source_order:
            source_documents = documents_by_source[source]

            if source_documents:
                reordered_documents.append(source_documents.pop(0))
                added_document = True

        if not added_document:
            break

    return reordered_documents


def apply_retrieval_strategy(
    documents: list[Document],
    retrieval_strategy: str,
) -> list[Document]:
    if retrieval_strategy == "skip_retrieval":
        return []

    if retrieval_strategy == "standard_retrieval":
        return documents

    if retrieval_strategy == "comparison_retrieval":
        return interleave_documents_by_source(documents)

    return documents


def build_retrieved_sources(documents: list[Document]) -> list[str]:
    sources: list[str] = []

    for document in documents:
        source = str(document.metadata.get("source", "unknown"))
        if source not in sources:
            sources.append(source)
    return sources


def build_trace_item(
    step: str,
    status: str = "completed",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "step": step,
        "status": status,
        "detail": detail or {},
    }


def build_sources(documents: list[Document]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []

    for doc in documents:
        sources.append(
            {
                "source": doc.metadata.get("source", "unknown"),
                "section_path": doc.metadata.get("section_path", "unknown"),
                "snippet": doc.page_content[:config.CHUNK_SIZE],
            }
        )

    return sources


def build_answer_cache_options(
    retrieval_plan: RetrievalPlan,
    search_type: RetrievalSearchType | None,
    fetch_k: int | None,
    lambda_mult: float | None,
) -> dict[str, Any]:
    return {
        "top_k": retrieval_plan.top_k,
        "retrieval_strategy": retrieval_plan.retrieval_strategy,
        "search_type": search_type or config.RETRIEVAL_SEARCH_TYPE,
        "fetch_k": fetch_k if fetch_k is not None else config.RETRIEVAL_FETCH_K,
        "lambda_mult": (
            lambda_mult
            if lambda_mult is not None
            else config.RETRIEVAL_LAMBDA_MULT
        ),
        "hybrid_candidate_k": config.RETRIEVAL_HYBRID_CANDIDATE_K,
        "hybrid_bm25_weight": config.RETRIEVAL_HYBRID_BM25_WEIGHT,
        "hybrid_dense_weight": config.RETRIEVAL_HYBRID_DENSE_WEIGHT,
        "qa_prompt_version": config.QA_PROMPT_VERSION,
        "llm_model_id": config.settings.llm_model_id,
        "llm_thinking_type": config.settings.llm_thinking_type,
    }


def build_retrieval_trace_detail(
    retrieval_plan: RetrievalPlan,
    duration_seconds: float,
    attempt: int | None = None,
    documents: list[Document] | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "retrieval_strategy": retrieval_plan.retrieval_strategy,
        "retrieval_query": retrieval_plan.retrieval_query,
        "top_k": retrieval_plan.top_k,
        "duration_seconds": round(duration_seconds, 2),
    }

    if attempt is not None:
        detail["attempt"] = attempt

    if documents is not None:
        detail["document_count"] = len(documents)
        detail["retrieved_sources"] = build_retrieved_sources(documents)

    if error is not None:
        detail["error_type"] = type(error).__name__

    return detail


def apply_top_k_override(
    retrieval_plan: RetrievalPlan,
    top_k: int | None,
) -> RetrievalPlan:
    if top_k is None or retrieval_plan.top_k == 0:
        return retrieval_plan

    return RetrievalPlan(
        retrieval_strategy=retrieval_plan.retrieval_strategy,
        retrieval_query=retrieval_plan.retrieval_query,
        top_k=top_k,
        reason=retrieval_plan.reason,
    )


def retrieve_documents_with_retry(
    retrieval_plan: RetrievalPlan,
    trace: list[dict[str, Any]],
    resources: AppResources | None = None,
    search_type: RetrievalSearchType | None = None,
    fetch_k: int | None = None,
    lambda_mult: float | None = None,
) -> list[Document]:
    max_attempts = config.MAX_RETRIEVAL_RETRY + 1
    last_error: Exception | None = None
    last_retrieval_duration_seconds = 0.0

    for attempt in range(1, max_attempts + 1):
        retrieval_start = perf_counter()

        try:
            retriever_kwargs: dict[str, Any] = {
                "top_k": retrieval_plan.top_k,
            }

            if (
                search_type is not None
                or fetch_k is not None
                or lambda_mult is not None
            ):
                retriever_kwargs.update(
                    {
                        "search_type": search_type,
                        "fetch_k": fetch_k,
                        "lambda_mult": lambda_mult,
                    }
                )

            if resources is None:
                retriever = get_retriever(**retriever_kwargs)
            else:
                retriever_kwargs["vector_store"] = resources.vector_store
                retriever = get_retriever(**retriever_kwargs)
            documents = retriever.invoke(retrieval_plan.retrieval_query)
        except Exception as exc:
            last_error = exc
            last_retrieval_duration_seconds = perf_counter() - retrieval_start

            if attempt < max_attempts:
                logger.warning(
                    "ask.retrieval retrying strategy=%s top_k=%s attempt=%s "
                    "duration_seconds=%.2f error_type=%s",
                    retrieval_plan.retrieval_strategy,
                    retrieval_plan.top_k,
                    attempt,
                    last_retrieval_duration_seconds,
                    type(exc).__name__,
                )
            else:
                logger.error(
                    "ask.retrieval failed strategy=%s top_k=%s attempt=%s "
                    "duration_seconds=%.2f error_type=%s",
                    retrieval_plan.retrieval_strategy,
                    retrieval_plan.top_k,
                    attempt,
                    last_retrieval_duration_seconds,
                    type(exc).__name__,
                )

            if attempt < max_attempts:
                trace.append(
                    build_trace_item(
                        step="retrieval",
                        status="retrying",
                        detail=build_retrieval_trace_detail(
                            retrieval_plan=retrieval_plan,
                            duration_seconds=last_retrieval_duration_seconds,
                            attempt=attempt,
                            error=exc,
                        ),
                    )
                )

            continue

        retrieval_duration_seconds = perf_counter() - retrieval_start
        documents = apply_retrieval_strategy(
            documents=documents,
            retrieval_strategy=retrieval_plan.retrieval_strategy,
        )

        logger.info(
            "ask.retrieval completed strategy=%s top_k=%s attempt=%s "
            "duration_seconds=%.2f document_count=%s",
            retrieval_plan.retrieval_strategy,
            retrieval_plan.top_k,
            attempt,
            retrieval_duration_seconds,
            len(documents),
        )

        attempt_detail = attempt if attempt > 1 else None
        trace.append(
            build_trace_item(
                step="retrieval",
                detail=build_retrieval_trace_detail(
                    retrieval_plan=retrieval_plan,
                    duration_seconds=retrieval_duration_seconds,
                    attempt=attempt_detail,
                    documents=documents,
                ),
            )
        )

        return documents

    if last_error is not None:
        trace.append(
            build_trace_item(
                step="retrieval",
                status="failed",
                detail=build_retrieval_trace_detail(
                    retrieval_plan=retrieval_plan,
                    duration_seconds=last_retrieval_duration_seconds,
                    attempt=max_attempts,
                    error=last_error,
                ),
            )
        )

    return []


def ask_question(
    question: str,
    resources: AppResources | None = None,
    top_k: int | None = None,
    search_type: RetrievalSearchType | None = None,
    fetch_k: int | None = None,
    lambda_mult: float | None = None,
    answer_cache: RedisAnswerCache | None = None,
) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []
    analysis = analyze_query(question)

    trace.append(
        build_trace_item(
            step="query_analysis",
            status="completed",
            detail={
                "normalized_question": analysis.normalized_question,
                "needs_retrieval": analysis.needs_retrieval,
                "reason": analysis.reason,
                "question_type": analysis.question_type,
            },
        )
    )

    retrieval_plan = apply_top_k_override(
        retrieval_plan=plan_retrieval(analysis),
        top_k=top_k,
    )

    trace.append(
        build_trace_item(
            step="retrieval_planning",
            status="completed",
            detail={
                "question_type": analysis.question_type,
                "retrieval_strategy": retrieval_plan.retrieval_strategy,
                "retrieval_query": retrieval_plan.retrieval_query,
                "top_k": retrieval_plan.top_k,
                "reason": retrieval_plan.reason,
            },
        )
    )

    if not analysis.needs_retrieval:
        trace.append(
            build_trace_item(
                step="retrieval",
                status="skipped",
                detail={
                    "retrieval_strategy": retrieval_plan.retrieval_strategy,
                    "retrieval_query": retrieval_plan.retrieval_query,
                    "top_k": retrieval_plan.top_k,
                    "reason": retrieval_plan.reason,
                },
            )
        )

        return {
            "answer": config.FALLBACK_ANSWER,
            "sources": [],
            "trace": trace,
        }

    active_cache = answer_cache if answer_cache is not None else get_answer_cache()
    cache_options = build_answer_cache_options(
        retrieval_plan=retrieval_plan,
        search_type=search_type,
        fetch_k=fetch_k,
        lambda_mult=lambda_mult,
    )
    if active_cache is not None:
        cache_lookup = active_cache.get_answer(
            analysis.normalized_question,
            cache_options,
        )
        trace.append(
            build_trace_item(
                step="answer_cache",
                status=cache_lookup.status,
                detail={"backend": "redis"},
            )
        )
        if cache_lookup.status == "hit" and cache_lookup.value is not None:
            return {
                "answer": cache_lookup.value["answer"],
                "sources": cache_lookup.value["sources"],
                "trace": trace,
            }

    documents = retrieve_documents_with_retry(
        retrieval_plan=retrieval_plan,
        trace=trace,
        resources=resources,
        search_type=search_type,
        fetch_k=fetch_k,
        lambda_mult=lambda_mult,
    )

    if not documents:
        return {
            "answer": config.FALLBACK_ANSWER,
            "sources": [],
            "trace": trace,
        }

    max_attempts = config.MAX_GENERATION_RETRY + 1
    last_error: Exception | None = None
    last_generation_duration_seconds: float = 0.0

    for attempt in range(1, max_attempts + 1):
        generation_start = perf_counter()
        try:
            context = format_context(documents)
            llm = resources.llm_client if resources is not None else get_client()
            prompt = get_qa_prompt()
            answer = generate_answer(
                question=analysis.normalized_question,
                context=context,
                prompt=prompt,
                llm=llm,
            )

            generation_duration_seconds = perf_counter() - generation_start

            logger.info(
                "ask.generation completed attempt=%s duration_seconds=%.2f "
                "source_count=%s",
                attempt,
                generation_duration_seconds,
                len(documents),
            )

            trace.append(
                build_trace_item(
                    step="generate_answer",
                    status="completed",
                    detail={
                        "attempt": attempt,
                        "duration_seconds": round(
                            generation_duration_seconds,
                            2,
                        ),
                    },
                )
            )

            response = {
                "answer": answer,
                "sources": build_sources(documents),
                "trace": trace,
            }
            if active_cache is not None:
                active_cache.set_answer(
                    analysis.normalized_question,
                    cache_options,
                    {
                        "answer": response["answer"],
                        "sources": response["sources"],
                    },
                    expected_version=cache_lookup.version,
                )
            return response

        except Exception as exc:
            last_error = exc
            last_generation_duration_seconds = perf_counter() - generation_start

            if attempt < max_attempts:
                trace.append(
                    build_trace_item(
                        step="generate_answer",
                        status="retrying",
                        detail={
                            "attempt": attempt,
                            "error_type": type(exc).__name__,
                            "duration_seconds": round(
                                last_generation_duration_seconds,
                                2,
                            ),
                        },
                    )
                )

                logger.warning(
                    "ask.generation retrying attempt=%s duration_seconds=%.2f "
                    "error_type=%s",
                    attempt,
                    last_generation_duration_seconds,
                    type(exc).__name__,
                )
            else:
                logger.error(
                    "ask.generation failed attempt=%s duration_seconds=%.2f "
                    "error_type=%s",
                    attempt,
                    last_generation_duration_seconds,
                    type(exc).__name__,
                )

    if last_error is not None:
        trace.append(
            build_trace_item(
                step="generate_answer",
                status="failed",
                detail={
                    "attempts": max_attempts,
                    "error_type": type(last_error).__name__,
                    "duration_seconds": round(
                        last_generation_duration_seconds,
                        2,
                    ),
                },
            )
        )

    return {
        "answer": config.FALLBACK_ANSWER,
        "sources": build_sources(documents),
        "trace": trace,
    }


def stream_ask_question(
    question: str,
    resources: AppResources | None = None,
    top_k: int | None = None,
    search_type: RetrievalSearchType | None = None,
    fetch_k: int | None = None,
    lambda_mult: float | None = None,
) -> Iterator[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    analysis = analyze_query(question)

    trace.append(
        build_trace_item(
            step="query_analysis",
            status="completed",
            detail={
                "normalized_question": analysis.normalized_question,
                "needs_retrieval": analysis.needs_retrieval,
                "reason": analysis.reason,
                "question_type": analysis.question_type,
            },
        )
    )

    retrieval_plan = apply_top_k_override(
        retrieval_plan=plan_retrieval(analysis),
        top_k=top_k,
    )

    trace.append(
        build_trace_item(
            step="retrieval_planning",
            status="completed",
            detail={
                "question_type": analysis.question_type,
                "retrieval_strategy": retrieval_plan.retrieval_strategy,
                "retrieval_query": retrieval_plan.retrieval_query,
                "top_k": retrieval_plan.top_k,
                "reason": retrieval_plan.reason,
            },
        )
    )

    if not analysis.needs_retrieval:
        trace.append(
            build_trace_item(
                step="retrieval",
                status="skipped",
                detail={
                    "retrieval_strategy": retrieval_plan.retrieval_strategy,
                    "retrieval_query": retrieval_plan.retrieval_query,
                    "top_k": retrieval_plan.top_k,
                    "reason": retrieval_plan.reason,
                },
            )
        )

        yield {"type": "answer_delta", "content": config.FALLBACK_ANSWER}
        yield {"type": "sources", "sources": []}
        yield {"type": "trace", "trace": trace}
        yield {"type": "done"}
        return

    documents = retrieve_documents_with_retry(
        retrieval_plan=retrieval_plan,
        trace=trace,
        resources=resources,
        search_type=search_type,
        fetch_k=fetch_k,
        lambda_mult=lambda_mult,
    )

    if not documents:
        yield {"type": "answer_delta", "content": config.FALLBACK_ANSWER}
        yield {"type": "sources", "sources": []}
        yield {"type": "trace", "trace": trace}
        yield {"type": "done"}
        return

    try:
        context = format_context(documents)
        llm = resources.llm_client if resources is not None else get_client()
        prompt = get_qa_prompt()

        for chunk in stream_answer(
            question=analysis.normalized_question,
            context=context,
            prompt=prompt,
            llm=llm,
        ):
            yield {
                "type": "answer_delta",
                "content": chunk,
            }

        trace.append(
            build_trace_item(
                step="generate_answer",
                status="completed",
                detail={"streaming": True},
            )
        )

        yield {"type": "sources", "sources": build_sources(documents)}
        yield {"type": "trace", "trace": trace}
        yield {"type": "done"}
        return

    except Exception as exc:
        trace.append(
            build_trace_item(
                step="generate_answer",
                status="failed",
                detail={
                    "streaming": True,
                    "error_type": type(exc).__name__,
                },
            )
        )

        yield {
            "type": "error",
            "error_type": type(exc).__name__,
            "message": config.FALLBACK_ANSWER,
        }
        yield {"type": "sources", "sources": build_sources(documents)}
        yield {"type": "trace", "trace": trace}
        yield {"type": "done"}
        return
