import json
import asyncio
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from rag_app.app.routers import ask as ask_router
from rag_app.infrastructure.resources import AppResources


def parse_ndjson(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line]


def test_ask_business_runs_in_a_tracked_worker(client, monkeypatch) -> None:
    from rag_app.infrastructure.request_concurrency import get_request_work
    observed = []

    def business(question, **kwargs):
        with pytest.raises(RuntimeError, match='no running event loop'):
            asyncio.get_running_loop()
        observed.append(get_request_work().snapshot()['workers'])
        return {'answer': 'offline', 'sources': [], 'trace': []}

    monkeypatch.setattr(ask_router, 'ask_question', business)
    assert client.post('/ask', json={'question': '问题'}).status_code == 200
    assert observed == [1]


def test_ask_returns_answer_and_sources(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    app_resources: AppResources,
) -> None:
    expected = {
        "answer": "LangChain 是一个用于构建 LLM 应用的框架。",
        "sources": [
            {
                "source": "data/raw/langchain-docs.md",
                "section_path": "Introduction > Overview",
                "snippet": "LangChain is a framework for developing applications.",
            }
        ],
        "trace": [],
    }

    mock_ask_question = Mock(return_value=expected)
    monkeypatch.setattr(ask_router, "ask_question", mock_ask_question)

    response = client.post("/ask", json={"question": "LangChain 是什么？"})

    assert response.status_code == 200
    assert response.json() == expected
    mock_ask_question.assert_called_once_with(
        "LangChain 是什么？",
        resources=app_resources,
    )


def test_ask_returns_422_when_question_is_missing(client: TestClient) -> None:

    response = client.post("/ask", json={})

    assert response.status_code == 422


def test_ask_stream_returns_ndjson_events(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    app_resources: AppResources,
) -> None:
    expected_events = [
        {
            "type": "answer_delta",
            "content": "LangChain 是",
        },
        {
            "type": "answer_delta",
            "content": "一个 LLM 应用框架。",
        },
        {
            "type": "sources",
            "sources": [
                {
                    "source": "data/raw/langchain-docs.md",
                    "section_path": "Introduction > Overview",
                    "snippet": "LangChain is a framework.",
                }
            ],
        },
        {
            "type": "trace",
            "trace": [
                {
                    "step": "generate_answer",
                    "status": "completed",
                    "detail": {"streaming": True},
                }
            ],
        },
        {
            "type": "done",
        },
    ]

    mock_stream_ask_question = Mock(return_value=iter(expected_events))
    monkeypatch.setattr(
        ask_router,
        "stream_ask_question",
        mock_stream_ask_question,
    )

    response = client.post(
        "/ask/stream",
        json={"question": "LangChain 是什么？"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert parse_ndjson(response.text) == expected_events
    mock_stream_ask_question.assert_called_once_with(
        question="LangChain 是什么？",
        resources=app_resources,
    )
