from unittest.mock import Mock

import pytest
from langchain_core.documents import Document

from rag_app.infrastructure.answer_cache import CacheLookup, RedisAnswerCache
from rag_app.infrastructure.request_budget import BudgetExceeded, RequestBudget, request_budget_scope
from rag_app.services import ask_service


def setup_rag(monkeypatch):
    retriever = Mock()
    retriever.invoke.return_value = [Document(page_content='evidence')]
    monkeypatch.setattr(ask_service, 'get_retriever', lambda **kw: retriever)
    monkeypatch.setattr(ask_service, 'get_client', lambda: Mock())
    monkeypatch.setattr(ask_service, 'get_answer_cache', lambda: None)
    return retriever


@pytest.mark.parametrize('streaming', [False, True])
def test_budget_errors_are_not_fallback_or_retry(monkeypatch, streaming):
    retriever = setup_rag(monkeypatch)
    retriever.invoke.side_effect = BudgetExceeded('context_budget_exhausted')
    with request_budget_scope(RequestBudget()), pytest.raises(BudgetExceeded):
        if streaming:
            list(ask_service.stream_ask_question('什么是 RAG？'))
        else:
            ask_service.ask_question('什么是 RAG？')
    assert retriever.invoke.call_count == 1


def test_late_generation_cannot_publish_cache(monkeypatch):
    setup_rag(monkeypatch)
    budget = RequestBudget()
    cache = Mock()
    cache.get_answer.return_value = CacheLookup(status='miss', version=1)

    def generate(**kwargs):
        budget.stop('task_timeout')
        return 'late answer'

    monkeypatch.setattr(ask_service, 'generate_answer', generate)
    with request_budget_scope(budget), pytest.raises(BudgetExceeded, match='task_timeout'):
        ask_service.ask_question('什么是 RAG？', answer_cache=cache)
    cache.set_answer.assert_not_called()


def test_generation_and_retrieval_do_not_retry_inside_request(monkeypatch):
    retriever = setup_rag(monkeypatch)
    generate = Mock(side_effect=RuntimeError('upstream failed'))
    monkeypatch.setattr(ask_service, 'generate_answer', generate)
    with request_budget_scope(RequestBudget()):
        ask_service.ask_question('什么是 RAG？')
    assert generate.call_count == 1
    retriever.invoke.side_effect = RuntimeError('retrieval failed')
    with request_budget_scope(RequestBudget()):
        ask_service.ask_question('什么是 RAG？')
    assert retriever.invoke.call_count == 2  # once per independent request


def test_cache_rechecks_after_its_own_version_lookup():
    budget = RequestBudget()
    client = Mock()

    def version(key):
        budget.stop('request_cancelled')
        return '1'

    client.get.side_effect = version
    cache = RedisAnswerCache(client, 'offline', 10)
    with request_budget_scope(budget), pytest.raises(BudgetExceeded):
        cache.set_answer('question', {}, {'answer': 'late', 'sources': []})
    client.set.assert_not_called()
