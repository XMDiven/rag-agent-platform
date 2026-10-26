"""Exercise production routes/factories and real ChatOpenAI over local HTTP fakes."""

import json
import asyncio
from contextlib import asynccontextmanager
from threading import Event
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.documents import Document

from agent_app.app import main as agent_main
from rag_app.app import main as rag_main
from rag_app.config import config
from rag_app.config.config import Settings
from rag_app.infrastructure.llm_client import get_client
from rag_app.infrastructure.model_transport import model_clients
from rag_app.infrastructure.request_budget import get_request_budget
from rag_app.infrastructure.resources import AppResources
from rag_app.services import ask_service


class Bytes(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data
    def __iter__(self):
        yield self.data
    async def __aiter__(self):
        yield self.data


def response(request, *, tool=False):
    payload = json.loads(request.content)
    message = {'role': 'assistant', 'content': '' if tool else 'offline answer'}
    if tool:
        message['tool_calls'] = [{'id': 'call_offline', 'type': 'function', 'function': {
            'name': 'retrieval_tool', 'arguments': '{"question":"什么是 RAG？"}',
        }}]
    reason = 'tool_calls' if tool else 'stop'
    usage = {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12}
    common = {'id': 'offline', 'created': 0, 'model': 'offline'}
    if payload.get('stream'):
        rows = [dict(common, object='chat.completion.chunk', choices=[
                    {'index': 0, 'delta': message, 'finish_reason': None}]),
                dict(common, object='chat.completion.chunk', choices=[
                    {'index': 0, 'delta': {}, 'finish_reason': reason}]),
                dict(common, object='chat.completion.chunk', choices=[], usage=usage)]
        if tool:
            rows[0]['choices'][0]['delta']['tool_calls'][0]['index'] = 0
        data = ''.join('data: ' + json.dumps(row) + '\n\n' for row in rows) + 'data: [DONE]\n\n'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Bytes(data.encode()))
    return httpx.Response(200, json=dict(common, object='chat.completion', usage=usage, choices=[
        {'index': 0, 'message': message, 'finish_reason': reason},
    ]))


def setup(monkeypatch, module, *, nested=False, context_bytes=32000):
    sent, budgets, pools = [], [], []
    settings = Settings(_env_file=None, llm_base_url='https://offline.invalid/v1',
                        llm_model_id='offline', openai_api_key='offline', moonshot_api_key=None,
                        request_max_model_calls=3 if nested else 1,
                        request_max_context_bytes_per_call=context_bytes)
    monkeypatch.setattr(config, 'settings', settings)

    def handle(request):
        budget = get_request_budget()
        assert budget is not None
        sent.append(json.loads(request.content))
        budgets.append(budget)
        return response(request, tool=nested and len(sent) == 1)

    @asynccontextmanager
    async def clients():
        async with model_clients(sync_transport=httpx.MockTransport(handle),
                                 async_transport=httpx.MockTransport(handle)) as pool:
            pools.append(pool)
            yield pool

    monkeypatch.setattr(module, 'model_clients', clients)
    if module is rag_main:
        monkeypatch.setattr(module, 'create_app_resources',
                            lambda: AppResources(llm_client=get_client(), vector_store=Mock()))
    retriever = Mock()
    retriever.invoke.return_value = [Document(page_content='offline evidence', metadata={'source': 'offline.md'})]
    monkeypatch.setattr(ask_service, 'get_retriever', lambda **kwargs: retriever)
    monkeypatch.setattr(ask_service, 'get_answer_cache', lambda: None)
    return sent, budgets, pools


@pytest.mark.parametrize('path,module', [('/ask', rag_main), ('/ask/stream', rag_main),
                                       ('/agent/run', agent_main), ('/agent/run/stream', agent_main)])
def test_four_actual_paths_share_guarded_app_clients_and_isolate_requests(monkeypatch, path, module):
    sent, budgets, pools = setup(monkeypatch, module)
    with TestClient(module.app) as client:
        for _ in range(2):
            result = client.post(path, json={'question': '什么是 RAG？'})
            assert result.status_code == 200
            if path.endswith('/stream'):
                events = [json.loads(line) for line in result.text.splitlines()]
                assert events[-1]['type'] == 'done'
                assert all(event['type'] != 'error' for event in events)
            else:
                assert result.json()['answer'] == 'offline answer'
        assert not pools[0].sync_client.is_closed
        assert not pools[0].async_client.is_closed
    assert pools[0].sync_client.is_closed and pools[0].async_client.is_closed
    assert len(sent) == 2 and all(body['max_tokens'] == 1024 for body in sent)
    assert budgets[0] is not budgets[1]
    assert all(budget.snapshot()['used_calls'] == 1 for budget in budgets)
    assert all(budget.snapshot()['used_output_tokens'] == 4 for budget in budgets)


@pytest.mark.parametrize('path', ['/agent/run', '/agent/run/stream'])
def test_nested_rag_tool_and_final_synthesis_use_one_budget(monkeypatch, path):
    sent, budgets, _ = setup(monkeypatch, agent_main, nested=True)
    with TestClient(agent_main.app) as client:
        result = client.post(path, json={'question': '什么是 RAG？'})
    assert result.status_code == 200
    assert len(sent) == 3  # planning + RAG tool generation + final synthesis
    assert sent[-1]['tool_choice'] == 'none'
    assert all(budget is budgets[0] for budget in budgets)
    assert budgets[0].snapshot()['used_output_tokens'] == 12
    if path.endswith('/stream'):
        events = [json.loads(line) for line in result.text.splitlines()]
        assert events[-1]['data']['termination_reason'] == 'final_answer'
        assert any(event['type'] == 'step' for event in events)
        assert not any(event['type'] == 'error' for event in events)


@pytest.mark.parametrize('path,module', [('/ask', rag_main), ('/ask/stream', rag_main),
                                       ('/agent/run', agent_main), ('/agent/run/stream', agent_main)])
def test_wire_budget_failure_is_explicit_and_preserves_headers(monkeypatch, path, module):
    sent, _, _ = setup(monkeypatch, module, context_bytes=1)
    with TestClient(module.app) as client:
        result = client.post(path, json={'question': '什么是 RAG？'},
                             headers={'origin': 'http://localhost:3000', 'x-request-id': 'budget-test'})
    assert sent == []
    assert result.headers['x-request-id'] == 'budget-test'
    assert result.headers['access-control-allow-origin'] == 'http://localhost:3000'
    if path.endswith('/stream'):
        assert result.status_code == 200
        events = [json.loads(line) for line in result.text.splitlines()]
        assert [event['type'] for event in events] == ['error', 'done']
        error = events[0].get('data', events[0])
        assert error['code'] == 'context_budget_exhausted'
        if path.startswith('/agent'):
            assert events[-1]['data']['termination_reason'] == 'failed'
        else:
            assert events[-1]['status'] == 'failed'
    else:
        assert result.status_code == 503
        assert result.json()['error']['code'] == 'context_budget_exhausted'


@pytest.mark.parametrize('path,module', [('/ask', rag_main), ('/ask/stream', rag_main),
                                       ('/agent/run', agent_main), ('/agent/run/stream', agent_main)])
def test_actual_model_wait_times_out_even_before_first_token(monkeypatch, path, module):
    sent, _, _ = setup(monkeypatch, module)
    monkeypatch.setattr(config.settings, 'request_timeout_seconds', .15)
    entered, release, finished = Event(), Event(), Event()

    def slow_sync(request):
        sent.append(json.loads(request.content))
        entered.set()
        try:
            release.wait(2)
            return response(request)
        finally:
            finished.set()

    async def slow_async(request):
        sent.append(json.loads(request.content))
        entered.set()
        await asyncio.Event().wait()

    @asynccontextmanager
    async def clients():
        async with model_clients(sync_transport=httpx.MockTransport(slow_sync),
                                 async_transport=httpx.MockTransport(slow_async)) as pool:
            yield pool

    monkeypatch.setattr(module, 'model_clients', clients)
    try:
        with TestClient(module.app) as client:
            result = client.post(path, json={'question': '什么是 RAG？'})
            assert entered.is_set()
            if path.endswith('/stream'):
                events = [json.loads(line) for line in result.text.splitlines()]
                assert [event['type'] for event in events] == ['error', 'done']
                assert events[0].get('data', events[0])['code'] == 'task_timeout'
            else:
                assert result.status_code == 504
                assert result.json()['error']['code'] == 'task_timeout'
            release.set()
            if path != '/agent/run/stream':
                assert finished.wait(1)
    finally:
        release.set()
    assert len(sent) == 1
