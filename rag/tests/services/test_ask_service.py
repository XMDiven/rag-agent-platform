from unittest.mock import Mock

from langchain_core.documents import Document

from rag_app.config import config
from rag_app.infrastructure.answer_cache import CacheLookup
from rag_app.infrastructure.resources import AppResources
from rag_app.services.ask_service import (
    apply_retrieval_strategy,
    ask_question,
    stream_ask_question,
)


def test_ask_question_plans_comparison_retrieval(monkeypatch) -> None:
    documents = [
        Document(
            page_content="LangChain is used for building LLM applications.",
            metadata={
                "source": "data/raw/langchain-docs.md",
                "section_path": "Overview",
            },
        )
    ]

    mock_retriever = Mock()
    mock_retriever.invoke.return_value = documents

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        lambda top_k=None: mock_retriever,
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.format_context",
        lambda docs: "formatted context",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_client",
        lambda: "fake-llm",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_qa_prompt",
        lambda: "fake-prompt",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.generate_answer",
        lambda question, context, prompt, llm: "LangChain 和 LlamaIndex 适合不同场景。",
    )

    result = ask_question("LangChain 和 LlamaIndex 分别适合做什么？")

    assert result["trace"][1]["detail"] == {
        "question_type": "comparison",
        "retrieval_strategy": "comparison_retrieval",
        "retrieval_query": "LangChain 和 LlamaIndex 分别适合做什么？",
        "top_k": config.RETRIEVAL_TOP_K,
        "reason": "comparison questions may need evidence from multiple sources",
    }
    assert result["trace"][2]["detail"]["retrieval_strategy"] == (
        "comparison_retrieval"
    )


def test_apply_comparison_retrieval_interleaves_documents_by_source() -> None:
    documents = [
        Document(page_content="A1", metadata={"source": "source-a.md"}),
        Document(page_content="A2", metadata={"source": "source-a.md"}),
        Document(page_content="B1", metadata={"source": "source-b.md"}),
        Document(page_content="C1", metadata={"source": "source-c.md"}),
    ]

    result = apply_retrieval_strategy(
        documents=documents,
        retrieval_strategy="comparison_retrieval",
    )

    assert [document.page_content for document in result] == [
        "A1",
        "B1",
        "C1",
        "A2",
    ]


def test_ask_question_returns_answer_and_sources(monkeypatch) -> None:
    documents = [
        Document(
            page_content="LangChain is a framework for developing applications.",
            metadata={
                "source": "data/raw/langchain-docs.md",
                "section_path": "Introduction > Overview",
            },
        )
    ]

    mock_retriever = Mock()
    mock_retriever.invoke.return_value = documents

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        lambda top_k=None: mock_retriever,
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.format_context",
        lambda docs: "formatted context",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_client",
        lambda: "fake-llm",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_qa_prompt",
        lambda: "fake-prompt",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.generate_answer",
        lambda question, context, prompt, llm: "LangChain 是一个用于构建 LLM 应用的框架。",
    )

    result = ask_question("LangChain 是什么？")

    assert result["answer"] == "LangChain 是一个用于构建 LLM 应用的框架。"
    assert result["sources"] == [
        {
            "source": "data/raw/langchain-docs.md",
            "section_path": "Introduction > Overview",
            "snippet": "LangChain is a framework for developing applications.",
        }
    ]
    assert [item["step"] for item in result["trace"]] == [
        "query_analysis",
        "retrieval_planning",
        "retrieval",
        "generate_answer",
    ]
    assert result["trace"][2]["detail"]["retrieved_sources"] == [
        "data/raw/langchain-docs.md"
    ]
    mock_retriever.invoke.assert_called_once_with("LangChain 是什么？")


def test_ask_question_returns_cached_answer_without_retrieval(monkeypatch) -> None:
    answer_cache = Mock()
    answer_cache.get_answer.return_value = CacheLookup(
        status="hit",
        value={
            "answer": "cached answer",
            "sources": [
                {
                    "source": "cached.md",
                    "section_path": "Overview",
                    "snippet": "cached evidence",
                }
            ],
        },
    )
    mock_get_retriever = Mock()
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        mock_get_retriever,
    )

    result = ask_question(
        "  What   is LangChain? ",
        answer_cache=answer_cache,
    )

    assert result["answer"] == "cached answer"
    assert result["sources"][0]["source"] == "cached.md"
    assert result["trace"][-1] == {
        "step": "answer_cache",
        "status": "hit",
        "detail": {"backend": "redis"},
    }
    answer_cache.get_answer.assert_called_once()
    assert answer_cache.get_answer.call_args.args[0] == "What is LangChain?"
    mock_get_retriever.assert_not_called()


def test_ask_question_caches_only_successful_generation(monkeypatch) -> None:
    documents = [
        Document(
            page_content="LangChain is a framework.",
            metadata={"source": "langchain.md", "section_path": "Overview"},
        )
    ]
    mock_retriever = Mock()
    mock_retriever.invoke.return_value = documents
    answer_cache = Mock()
    answer_cache.get_answer.return_value = CacheLookup(status="miss")

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        lambda **kwargs: mock_retriever,
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.format_context",
        lambda docs: "formatted context",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_client",
        lambda: "fake-llm",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_qa_prompt",
        lambda: "fake-prompt",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.generate_answer",
        lambda **kwargs: "generated answer",
    )

    result = ask_question("What is LangChain?", answer_cache=answer_cache)

    assert result["answer"] == "generated answer"
    assert result["trace"][2] == {
        "step": "answer_cache",
        "status": "miss",
        "detail": {"backend": "redis"},
    }
    answer_cache.set_answer.assert_called_once()
    assert answer_cache.set_answer.call_args.args[0] == "What is LangChain?"
    assert answer_cache.set_answer.call_args.args[2] == {
        "answer": "generated answer",
        "sources": [
            {
                "source": "langchain.md",
                "section_path": "Overview",
                "snippet": "LangChain is a framework.",
            }
        ],
    }


def test_ask_question_passes_mmr_retrieval_options(monkeypatch) -> None:
    documents = [
        Document(
            page_content="LangChain is a framework for developing applications.",
            metadata={
                "source": "data/raw/langchain-docs.md",
                "section_path": "Introduction > Overview",
            },
        )
    ]

    mock_retriever = Mock()
    mock_retriever.invoke.return_value = documents
    mock_get_retriever = Mock(return_value=mock_retriever)

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        mock_get_retriever,
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.format_context",
        lambda docs: "formatted context",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_client",
        lambda: "fake-llm",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_qa_prompt",
        lambda: "fake-prompt",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.generate_answer",
        lambda question, context, prompt, llm: "LangChain 是一个用于构建 LLM 应用的框架。",
    )

    ask_question(
        "LangChain 是什么？",
        search_type="mmr",
        fetch_k=50,
        lambda_mult=0.3,
    )

    mock_get_retriever.assert_called_once_with(
        top_k=config.RETRIEVAL_TOP_K,
        search_type="mmr",
        fetch_k=50,
        lambda_mult=0.3,
    )


def test_ask_question_returns_fallback_when_answer_generation_fails(
    monkeypatch,
) -> None:
    documents = [
        Document(
            page_content="LangChain is a framework for developing applications.",
            metadata={
                "source": "data/raw/langchain-docs.md",
                "section_path": "Introduction > Overview",
            },
        )
    ]

    mock_retriever = Mock()
    mock_retriever.invoke.return_value = documents

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        lambda top_k=None: mock_retriever,
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.format_context",
        lambda docs: "formatted context",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_client",
        lambda: "fake-llm",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_qa_prompt",
        lambda: "fake-prompt",
    )

    def raise_generation_error(question, context, prompt, llm):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(
        "rag_app.services.ask_service.generate_answer",
        raise_generation_error,
    )

    result = ask_question("LangChain 是什么？")

    assert result["answer"] == config.FALLBACK_ANSWER
    assert result["sources"] == [
        {
            "source": "data/raw/langchain-docs.md",
            "section_path": "Introduction > Overview",
            "snippet": "LangChain is a framework for developing applications.",
        }
    ]
    failed_trace = result["trace"][-1]

    assert failed_trace["step"] == "generate_answer"
    assert failed_trace["status"] == "failed"
    assert failed_trace["detail"]["attempts"] == config.MAX_GENERATION_RETRY + 1
    assert failed_trace["detail"]["error_type"] == "RuntimeError"
    assert "duration_seconds" in failed_trace["detail"]
    mock_retriever.invoke.assert_called_once_with("LangChain 是什么？")


def test_ask_question_retries_answer_generation_once(monkeypatch) -> None:
    documents = [
        Document(
            page_content="LangChain is a framework for developing applications.",
            metadata={
                "source": "data/raw/langchain-docs.md",
                "section_path": "Introduction > Overview",
            },
        )
    ]

    mock_retriever = Mock()
    mock_retriever.invoke.return_value = documents
    mock_generate_answer = Mock(
        side_effect=[
            RuntimeError("temporary LLM failure"),
            "LangChain 是一个用于构建 LLM 应用的框架。",
        ]
    )

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        lambda top_k=None: mock_retriever,
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.format_context",
        lambda docs: "formatted context",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_client",
        lambda: "fake-llm",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_qa_prompt",
        lambda: "fake-prompt",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.generate_answer",
        mock_generate_answer,
    )

    result = ask_question("LangChain 是什么？")

    assert result["answer"] == "LangChain 是一个用于构建 LLM 应用的框架。"
    assert mock_generate_answer.call_count == 2
    retry_trace = result["trace"][-2]

    assert retry_trace["step"] == "generate_answer"
    assert retry_trace["status"] == "retrying"
    assert retry_trace["detail"]["attempt"] == 1
    assert retry_trace["detail"]["error_type"] == "RuntimeError"
    assert "duration_seconds" in retry_trace["detail"]
    assert result["trace"][-1] == {
        "step": "generate_answer",
        "status": "completed",
        "detail": {
            "attempt": 2,
            "duration_seconds": 0.0,
        },
    }


def test_ask_question_retries_retrieval_once(monkeypatch) -> None:
    documents = [
        Document(
            page_content="LangChain is a framework for developing applications.",
            metadata={
                "source": "data/raw/langchain-docs.md",
                "section_path": "Introduction > Overview",
            },
        )
    ]

    mock_retriever = Mock()
    mock_retriever.invoke.side_effect = [
        RuntimeError("temporary qdrant failure"),
        documents,
    ]

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        lambda top_k=None: mock_retriever,
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.format_context",
        lambda docs: "formatted context",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_client",
        lambda: "fake-llm",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_qa_prompt",
        lambda: "fake-prompt",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.generate_answer",
        lambda question, context, prompt, llm: "LangChain 是一个用于构建 LLM 应用的框架。",
    )

    result = ask_question("LangChain 是什么？")

    assert result["answer"] == "LangChain 是一个用于构建 LLM 应用的框架。"
    assert mock_retriever.invoke.call_count == 2

    retry_trace = result["trace"][2]
    assert retry_trace["step"] == "retrieval"
    assert retry_trace["status"] == "retrying"
    assert retry_trace["detail"]["attempt"] == 1
    assert retry_trace["detail"]["error_type"] == "RuntimeError"
    assert retry_trace["detail"]["retrieval_query"] == "LangChain 是什么？"
    assert retry_trace["detail"]["top_k"] == config.RETRIEVAL_TOP_K
    assert "duration_seconds" in retry_trace["detail"]

    completed_trace = result["trace"][3]
    assert completed_trace["step"] == "retrieval"
    assert completed_trace["status"] == "completed"
    assert completed_trace["detail"]["attempt"] == 2
    assert completed_trace["detail"]["document_count"] == 1


def test_ask_question_returns_fallback_when_retrieval_fails(
    monkeypatch,
) -> None:
    mock_retriever = Mock()
    mock_retriever.invoke.side_effect = RuntimeError("qdrant unavailable")

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        lambda top_k=None: mock_retriever,
    )

    result = ask_question("LangChain 是什么？")

    assert result["answer"] == config.FALLBACK_ANSWER
    assert result["sources"] == []
    assert mock_retriever.invoke.call_count == config.MAX_RETRIEVAL_RETRY + 1

    failed_trace = result["trace"][-1]
    assert failed_trace["step"] == "retrieval"
    assert failed_trace["status"] == "failed"
    assert failed_trace["detail"]["attempt"] == config.MAX_RETRIEVAL_RETRY + 1
    assert failed_trace["detail"]["error_type"] == "RuntimeError"
    assert failed_trace["detail"]["retrieval_query"] == "LangChain 是什么？"


def test_ask_question_returns_fallback_when_no_documents(monkeypatch) -> None:
    mock_retriever = Mock()
    mock_retriever.invoke.return_value = []

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        lambda top_k=None: mock_retriever,
    )

    result = ask_question("一个文档里完全没有的问题")

    assert result == {
        "answer": config.FALLBACK_ANSWER,
        "sources": [],
        "trace": [
            {
                "step": "query_analysis",
                "status": "completed",
                "detail": {
                    "normalized_question": "一个文档里完全没有的问题",
                    "needs_retrieval": True,
                    "reason": "normal knowledge question, use retrieval",
                    "question_type": "general",
                },
            },
            {
                "step": "retrieval_planning",
                "status": "completed",
                "detail": {
                    "question_type": "general",
                    "retrieval_strategy": "standard_retrieval",
                    "retrieval_query": "一个文档里完全没有的问题",
                    "top_k": config.RETRIEVAL_TOP_K,
                    "reason": "general knowledge questions use standard retrieval",
                },
            },
            {
                "step": "retrieval",
                "status": "completed",
                "detail": {
                    "retrieval_strategy": "standard_retrieval",
                    "retrieval_query": "一个文档里完全没有的问题",
                    "top_k": config.RETRIEVAL_TOP_K,
                    "document_count": 0,
                    "retrieved_sources": [],
                    "duration_seconds": 0.0,
                },
            },
        ],
    }
    mock_retriever.invoke.assert_called_once_with("一个文档里完全没有的问题")


def test_stream_ask_question_streams_answer_chunks(monkeypatch) -> None:
    documents = [
        Document(
            page_content="LangChain is a framework for developing applications.",
            metadata={
                "source": "data/raw/langchain-docs.md",
                "section_path": "Introduction > Overview",
            },
        )
    ]

    mock_retriever = Mock()
    mock_retriever.invoke.return_value = documents
    mock_stream_answer = Mock(return_value=iter(["LangChain 是", "一个框架。"]))

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        lambda top_k=None: mock_retriever,
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.format_context",
        lambda docs: "formatted context",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_client",
        lambda: "fake-llm",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_qa_prompt",
        lambda: "fake-prompt",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.stream_answer",
        mock_stream_answer,
    )

    events = list(stream_ask_question("LangChain 是什么？"))

    assert events[:2] == [
        {
            "type": "answer_delta",
            "content": "LangChain 是",
        },
        {
            "type": "answer_delta",
            "content": "一个框架。",
        },
    ]
    assert events[-3:] == [
        {
            "type": "sources",
            "sources": [
                {
                    "source": "data/raw/langchain-docs.md",
                    "section_path": "Introduction > Overview",
                    "snippet": "LangChain is a framework for developing applications.",
                }
            ],
        },
        {
            "type": "trace",
            "trace": [
                {
                    "step": "query_analysis",
                    "status": "completed",
                    "detail": {
                        "normalized_question": "LangChain 是什么？",
                        "needs_retrieval": True,
                        "reason": "normal knowledge question, use retrieval",
                        "question_type": "general",
                    },
                },
                {
                    "step": "retrieval_planning",
                    "status": "completed",
                    "detail": {
                        "question_type": "general",
                        "retrieval_strategy": "standard_retrieval",
                        "retrieval_query": "LangChain 是什么？",
                        "top_k": config.RETRIEVAL_TOP_K,
                        "reason": "general knowledge questions use standard retrieval",
                    },
                },
                {
                    "step": "retrieval",
                    "status": "completed",
                    "detail": {
                        "retrieval_strategy": "standard_retrieval",
                        "retrieval_query": "LangChain 是什么？",
                        "top_k": config.RETRIEVAL_TOP_K,
                        "document_count": 1,
                        "retrieved_sources": ["data/raw/langchain-docs.md"],
                        "duration_seconds": 0.0,
                    },
                },
                {
                    "step": "generate_answer",
                    "status": "completed",
                    "detail": {"streaming": True},
                },
            ],
        },
        {
            "type": "done",
        },
    ]
    mock_stream_answer.assert_called_once_with(
        question="LangChain 是什么？",
        context="formatted context",
        prompt="fake-prompt",
        llm="fake-llm",
    )
    mock_retriever.invoke.assert_called_once_with("LangChain 是什么？")


def test_stream_ask_question_uses_resources(monkeypatch) -> None:
    monkeypatch.setattr(config, "RETRIEVAL_SEARCH_TYPE", "mmr")
    monkeypatch.setattr(config, "RETRIEVAL_TOP_K", 7)
    monkeypatch.setattr(config, "RETRIEVAL_FETCH_K", 50)
    monkeypatch.setattr(config, "RETRIEVAL_LAMBDA_MULT", 0.3)

    documents = [
        Document(
            page_content="LangChain is a framework for developing applications.",
            metadata={
                "source": "data/raw/langchain-docs.md",
                "section_path": "Introduction > Overview",
            },
        )
    ]

    mock_retriever = Mock()
    mock_retriever.invoke.return_value = documents
    mock_vector_store = Mock()
    mock_vector_store.as_retriever.return_value = mock_retriever
    mock_llm = Mock()
    mock_stream_answer = Mock(return_value=iter(["LangChain 是一个框架。"]))
    mock_get_client = Mock(
        side_effect=AssertionError("get_client should not be called")
    )
    resources = AppResources(
        llm_client=mock_llm,
        vector_store=mock_vector_store,
    )

    monkeypatch.setattr(
        "rag_app.services.ask_service.format_context",
        lambda docs: "formatted context",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_client",
        mock_get_client,
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_qa_prompt",
        lambda: "fake-prompt",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.stream_answer",
        mock_stream_answer,
    )

    events = list(
        stream_ask_question(
            "LangChain 是什么？",
            resources=resources,
        )
    )

    assert events[0] == {
        "type": "answer_delta",
        "content": "LangChain 是一个框架。",
    }
    mock_vector_store.as_retriever.assert_called_once_with(
        search_type="mmr",
        search_kwargs={
            "k": config.RETRIEVAL_TOP_K,
            "fetch_k": config.RETRIEVAL_FETCH_K,
            "lambda_mult": config.RETRIEVAL_LAMBDA_MULT,
        },
    )
    mock_retriever.invoke.assert_called_once_with("LangChain 是什么？")
    mock_get_client.assert_not_called()
    mock_stream_answer.assert_called_once_with(
        question="LangChain 是什么？",
        context="formatted context",
        prompt="fake-prompt",
        llm=mock_llm,
    )


def test_stream_ask_question_retries_retrieval_once(monkeypatch) -> None:
    documents = [
        Document(
            page_content="LangChain is a framework for developing applications.",
            metadata={
                "source": "data/raw/langchain-docs.md",
                "section_path": "Introduction > Overview",
            },
        )
    ]

    mock_retriever = Mock()
    mock_retriever.invoke.side_effect = [
        RuntimeError("temporary qdrant failure"),
        documents,
    ]
    mock_stream_answer = Mock(return_value=iter(["LangChain 是一个框架。"]))

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        lambda top_k=None: mock_retriever,
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.format_context",
        lambda docs: "formatted context",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_client",
        lambda: "fake-llm",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.get_qa_prompt",
        lambda: "fake-prompt",
    )
    monkeypatch.setattr(
        "rag_app.services.ask_service.stream_answer",
        mock_stream_answer,
    )

    events = list(stream_ask_question("LangChain 是什么？"))

    assert events[0] == {
        "type": "answer_delta",
        "content": "LangChain 是一个框架。",
    }
    assert mock_retriever.invoke.call_count == 2

    trace_event = next(event for event in events if event["type"] == "trace")
    retry_trace = trace_event["trace"][2]
    completed_trace = trace_event["trace"][3]

    assert retry_trace["step"] == "retrieval"
    assert retry_trace["status"] == "retrying"
    assert retry_trace["detail"]["attempt"] == 1
    assert retry_trace["detail"]["error_type"] == "RuntimeError"
    assert retry_trace["detail"]["retrieval_query"] == "LangChain 是什么？"
    assert retry_trace["detail"]["top_k"] == config.RETRIEVAL_TOP_K
    assert "duration_seconds" in retry_trace["detail"]

    assert completed_trace["step"] == "retrieval"
    assert completed_trace["status"] == "completed"
    assert completed_trace["detail"]["attempt"] == 2
    assert completed_trace["detail"]["document_count"] == 1
    mock_stream_answer.assert_called_once_with(
        question="LangChain 是什么？",
        context="formatted context",
        prompt="fake-prompt",
        llm="fake-llm",
    )


def test_ask_question_skips_retrieval_when_question_is_empty(monkeypatch) -> None:
    mock_retriever = Mock()

    monkeypatch.setattr(
        "rag_app.services.ask_service.get_retriever",
        lambda top_k=None: mock_retriever,
    )

    result = ask_question("   ")

    assert result == {
        "answer": config.FALLBACK_ANSWER,
        "sources": [],
        "trace": [
            {
                "step": "query_analysis",
                "status": "completed",
                "detail": {
                    "normalized_question": "",
                    "needs_retrieval": False,
                    "reason": "empty question",
                    "question_type": "empty",
                },
            },
            {
                "step": "retrieval_planning",
                "status": "completed",
                "detail": {
                    "question_type": "empty",
                    "retrieval_strategy": "skip_retrieval",
                    "retrieval_query": "",
                    "top_k": 0,
                    "reason": "empty questions do not need retrieval",
                },
            },
            {
                "step": "retrieval",
                "status": "skipped",
                "detail": {
                    "retrieval_strategy": "skip_retrieval",
                    "retrieval_query": "",
                    "top_k": 0,
                    "reason": "empty questions do not need retrieval",
                },
            },
        ],
    }
    mock_retriever.invoke.assert_not_called()
