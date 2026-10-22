import pytest

from rag_app.scripts.evaluate_answers import (
    answer_has_forbidden_fragments,
    answer_has_required_v2_sections,
    evaluate_answer_result,
    get_answer_source_paths,
)
from rag_app.config import config
from rag_app.scripts.evaluate_retrieval import RetrievalEvalCase


def test_get_answer_source_paths_extracts_sources_from_answer_result() -> None:
    result = {
        "answer": "Qdrant is used for vector search. [1]",
        "sources": [
            {
                "source": "data/raw/qdrant-docs.md",
                "section_path": "unknown",
                "snippet": "Qdrant is a vector search engine.",
            }
        ],
    }

    assert get_answer_source_paths(result) == ["data/raw/qdrant-docs.md"]


def test_answer_has_forbidden_fragments_detects_source_metadata() -> None:
    answer = "RAG combines retrieval and generation. source:data/raw/rag.pdf"

    assert answer_has_forbidden_fragments(answer)


def test_answer_has_forbidden_fragments_allows_numbered_citations() -> None:
    answer = "RAG combines retrieval and generation. [1]"

    assert not answer_has_forbidden_fragments(answer)


def test_evaluate_answer_result_passes_when_answer_and_sources_are_valid() -> None:
    case = RetrievalEvalCase(
        id="qdrant_usage",
        question="What is Qdrant used for?",
        expected_source_contains=["qdrant-docs.md"],
    )
    result = {
        "answer": "Qdrant is used for vector search. [1]",
        "sources": [
            {
                "source": "data/raw/qdrant-docs.md",
                "section_path": "unknown",
                "snippet": "Qdrant is a vector search engine.",
            }
        ],
    }

    passed, failures = evaluate_answer_result(case, result)

    assert passed
    assert failures == []


def test_evaluate_answer_result_fails_when_expected_source_is_missing() -> None:
    case = RetrievalEvalCase(
        id="qdrant_usage",
        question="What is Qdrant used for?",
        expected_source_contains=["qdrant-docs.md"],
    )
    result = {
        "answer": "Qdrant is used for vector search. [1]",
        "sources": [
            {
                "source": "data/raw/langchain-docs.md",
                "section_path": "unknown",
                "snippet": "LangChain docs.",
            }
        ],
    }

    passed, failures = evaluate_answer_result(case, result)

    assert not passed
    assert "expected source not found" in failures


@pytest.mark.parametrize(
    ("match", "source_paths", "expected_passed"),
    [
        pytest.param(
            "all", ["langchain-docs.md"], False, id="all-missing-source"
        ),
        pytest.param(
            "all",
            ["langchain-docs.md", "llamaindex-docs.md"],
            True,
            id="all-sources-present",
        ),
        pytest.param(
            "any", ["langchain-docs.md"], True, id="any-one-source-present"
        ),
    ],
)
def test_evaluate_answer_result_respects_source_match_mode(
    monkeypatch, match: str, source_paths: list[str], expected_passed: bool
) -> None:
    monkeypatch.setattr(config, "QA_PROMPT_VERSION", "qa_prompt_v1")
    case = RetrievalEvalCase(
        id="framework_comparison",
        question="Compare LangChain and LlamaIndex.",
        expected_source_contains=["langchain-docs.md", "llamaindex-docs.md"],
        match=match,
    )
    result = {
        "answer": "Both frameworks support retrieval workflows. [1]",
        "sources": [{"source": path} for path in source_paths],
    }

    passed, failures = evaluate_answer_result(case, result)

    assert passed is expected_passed
    assert failures == ([] if expected_passed else ["expected source not found"])


def test_evaluate_answer_result_fails_when_answer_is_fallback() -> None:
    case = RetrievalEvalCase(
        id="qdrant_usage",
        question="What is Qdrant used for?",
        expected_source_contains=["qdrant-docs.md"],
    )
    result = {
        "answer": config.FALLBACK_ANSWER,
        "sources": [
            {
                "source": "data/raw/qdrant-docs.md",
                "section_path": "unknown",
                "snippet": "Qdrant is a vector search engine.",
            }
        ],
    }

    passed, failures = evaluate_answer_result(case, result)

    assert not passed
    assert "answer is fallback" in failures


def test_evaluate_answer_result_fails_when_generation_trace_failed() -> None:
    case = RetrievalEvalCase(
        id="qdrant_usage",
        question="What is Qdrant used for?",
        expected_source_contains=["qdrant-docs.md"],
    )
    result = {
        "answer": "Qdrant is used for vector search. [1]",
        "sources": [
            {
                "source": "data/raw/qdrant-docs.md",
                "section_path": "unknown",
                "snippet": "Qdrant is a vector search engine.",
            }
        ],
        "trace": [
            {
                "step": "generate_answer",
                "status": "failed",
                "detail": {
                    "attempts": 2,
                    "error_type": "RateLimitError",
                },
            }
        ],
    }

    passed, failures = evaluate_answer_result(case, result)

    assert not passed
    assert "answer generation failed" in failures


def test_answer_has_required_v2_sections_accepts_structured_answer() -> None:
    answer = (
        "Direct answer:\n"
        "- RAG combines retrieval and generation. [1]\n\n"
        "Key evidence:\n"
        "- It retrieves external context. [1]\n\n"
        "Limitations:\n"
        "- No major limitation from the provided context."
    )
    assert answer_has_required_v2_sections(answer)


def test_answer_has_required_v2_sections_rejects_missing_section() -> None:
    answer = (
        "Direct answer:\n"
        "- RAG combines retrieval and generation. [1]\n\n"
        "Key evidence:\n"
        "- It retrieves external context. [1]"
    )

    assert not answer_has_required_v2_sections(answer)


def test_evaluate_answer_result_fails_when_v2_structure_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "QA_PROMPT_VERSION", "qa_prompt_v2")

    case = RetrievalEvalCase(
        id="rag_definition",
        question="What is RAG?",
        expected_source_contains=["rag.pdf"],
    )
    result = {
        "answer": "RAG combines retrieval and generation. [1]",
        "sources": [
            {
                "source": "data/raw/rag.pdf",
                "section_path": "unknown",
                "snippet": "RAG combines retrieval and generation.",
            }
        ],
        "trace": [],
    }

    passed, failures = evaluate_answer_result(case, result)

    assert not passed
    assert "answer does not match qa_prompt_v2 structure" in failures


def test_evaluate_answer_result_does_not_require_v2_structure_for_v1(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "QA_PROMPT_VERSION", "qa_prompt_v1")

    case = RetrievalEvalCase(
        id="rag_definition",
        question="What is RAG?",
        expected_source_contains=["rag.pdf"],
    )
    result = {
        "answer": "RAG combines retrieval and generation. [1]",
        "sources": [
            {
                "source": "data/raw/rag.pdf",
                "section_path": "unknown",
                "snippet": "RAG combines retrieval and generation.",
            }
        ],
        "trace": [],
    }

    passed, failures = evaluate_answer_result(case, result)

    assert passed
    assert "answer does not match qa_prompt_v2 structure" not in failures
