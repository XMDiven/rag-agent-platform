import asyncio
import json
import gzip

import httpx
import pytest
from langchain_openai import ChatOpenAI

from rag_app.infrastructure.model_transport import (
    AsyncBudgetTransport, BudgetTransport, get_model_clients, model_clients,
    model_clients_scope,
)
from rag_app.infrastructure.request_budget import BudgetExceeded, RequestBudget, request_budget_scope


def completion(usage=5, finish='stop'):
    payload = {'id': 'offline', 'object': 'chat.completion', 'created': 0, 'model': 'offline',
               'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': finish}]}
    if usage is not None:
        payload['usage'] = {'prompt_tokens': 2, 'completion_tokens': usage, 'total_tokens': usage + 2}
    return payload


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('streaming', [False, True])
def test_compressed_provider_usage_is_decoded_before_accounting(asynchronous, streaming):
    budget = RequestBudget()
    raw = b''.join(events()) if streaming else json.dumps(completion()).encode()
    stream = Chunks([gzip.compress(raw)])

    def handle(request):
        return httpx.Response(200, stream=stream, headers={
            'content-encoding': 'gzip',
            'content-type': 'text/event-stream' if streaming else 'application/json',
        })

    async def run():
        async with httpx.AsyncClient(transport=AsyncBudgetTransport(httpx.MockTransport(handle))) as client:
            model = chat(async_client=client)
            if streaming:
                assert ''.join([part.content async for part in model.astream('hello')]) == 'ok'
            else:
                assert (await model.ainvoke('hello')).content == 'ok'

    with request_budget_scope(budget):
        if asynchronous:
            asyncio.run(run())
        else:
            with httpx.Client(transport=BudgetTransport(httpx.MockTransport(handle))) as client:
                model = chat(client)
                if streaming:
                    assert ''.join(part.content for part in model.stream('hello')) == 'ok'
                else:
                    assert model.invoke('hello').content == 'ok'
    assert budget.snapshot()['used_output_tokens'] == (3 if streaming else 5)
    assert stream.closed


def chat(sync_client=None, async_client=None):
    return ChatOpenAI(model='offline', api_key='offline-key', base_url='https://offline.invalid/v1',
                      http_client=sync_client, http_async_client=async_client, max_retries=0,
                      stream_usage=True)


def test_real_chat_json_caps_wire_body_and_settles_usage():
    sent = []
    def send(request):
        sent.append(request)
        return httpx.Response(200, json=completion())
    budget = RequestBudget()
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(send))) as client:
        with request_budget_scope(budget):
            assert chat(client).invoke('hello').content == 'ok'
    assert json.loads(sent[0].content)['max_tokens'] == 1024
    assert 0 < sent[0].extensions['timeout']['read'] <= 90
    assert budget.snapshot()['used_output_tokens'] == 5


def test_seventh_http_request_is_blocked_before_forward():
    sent = []
    def send(request):
        sent.append(request)
        return httpx.Response(200, json=completion(0))
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(send))) as client:
        with request_budget_scope(RequestBudget()):
            for _ in range(6):
                client.post('https://offline.invalid', json={'messages': []})
            with pytest.raises(BudgetExceeded, match='model_calls_exhausted'):
                client.post('https://offline.invalid', json={'messages': []})
    assert len(sent) == 6


@pytest.mark.parametrize('status,usage', [(500, 0), (200, None)])
def test_failed_or_missing_usage_keeps_output_reservation(status, usage):
    budget = RequestBudget()
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(
        lambda request: httpx.Response(status, json=completion(usage)),
    ))) as client:
        with request_budget_scope(budget):
            client.post('https://offline.invalid', json={'messages': []})
    assert budget.snapshot()['used_output_tokens'] == 1024


class Chunks(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
    def __iter__(self):
        yield from self.chunks
    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
    def close(self):
        self.closed = True
    async def aclose(self):
        self.closed = True


def events(usage=True, finish='stop'):
    rows = [{'id': 'x', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'offline',
             'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': None}]},
            {'id': 'x', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}]
    if usage:
        rows.append({'id': 'x', 'choices': [], 'usage': {'completion_tokens': 3, 'prompt_tokens': 2, 'total_tokens': 5}})
    data = ''.join('data: ' + json.dumps(row) + '\r\n\r\n' for row in rows) + 'data: [DONE]\r\n\r\n'
    return [data[i:i+7].encode() for i in range(0, len(data), 7)]


def test_real_chat_sse_split_usage_is_incremental_and_settled():
    budget = RequestBudget()
    stream = Chunks(events())
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(lambda request:
            httpx.Response(200, stream=stream, headers={'content-type': 'text/event-stream'})))) as client:
        with request_budget_scope(budget):
            assert ''.join(part.content for part in chat(client).stream('hello')) == 'ok'
    assert stream.closed
    assert budget.snapshot()['used_output_tokens'] == 3


def test_early_stream_close_keeps_reservation():
    budget = RequestBudget()
    stream = Chunks(events())
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(lambda request:
            httpx.Response(200, stream=stream, headers={'content-type': 'text/event-stream'})))) as client:
        with request_budget_scope(budget):
            with client.stream('POST', 'https://offline.invalid', json={'messages': []}) as response:
                next(response.iter_bytes())
    assert budget.snapshot()['used_output_tokens'] == 1024
    assert stream.closed


@pytest.mark.parametrize('streaming', [False, True])
def test_length_finish_is_budget_failure(streaming):
    budget = RequestBudget()
    def send(request):
        if streaming:
            return httpx.Response(200, stream=Chunks(events(finish='length')), headers={'content-type': 'text/event-stream'})
        return httpx.Response(200, json=completion(finish='length'))
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(send))) as client:
        with request_budget_scope(budget), pytest.raises(BudgetExceeded, match='output_tokens_exhausted'):
            client.post('https://offline.invalid', json={'messages': []})


def test_deadline_after_io_closes_response():
    now = [0]
    budget = RequestBudget(timeout_seconds=1, clock=lambda: now[0])
    stream = Chunks([b'{}'])
    def send(request):
        now[0] = 2
        return httpx.Response(200, stream=stream)
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(send))) as client:
        with request_budget_scope(budget), pytest.raises(BudgetExceeded, match='task_timeout'):
            client.post('https://offline.invalid', json={})
    assert stream.closed


def test_no_scope_passes_same_request_and_existing_cap_conflict_is_removed():
    sent = []
    def send(request):
        sent.append(request)
        return httpx.Response(200, json=completion())
    transport = BudgetTransport(httpx.MockTransport(send))
    request = httpx.Request('POST', 'https://offline.invalid', json={'max_completion_tokens': 9999})
    transport.handle_request(request).close()
    assert sent[0] is request
    with request_budget_scope(RequestBudget()):
        response = transport.handle_request(request)
        response.read()
        response.close()
    body = json.loads(sent[1].content)
    assert body['max_tokens'] == 1024
    assert 'max_completion_tokens' not in body


def test_async_real_chat_and_bundle_closure_and_scope():
    async def run():
        budget = RequestBudget()
        async with model_clients(sync_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=completion())),
                                 async_transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Chunks(events()),
                                     headers={'content-type': 'text/event-stream'}))) as clients:
            with model_clients_scope(clients):
                assert get_model_clients() is clients
                with request_budget_scope(budget):
                    llm = chat(clients.sync_client, clients.async_client)
                    assert ''.join([part.content async for part in llm.astream('hello')]) == 'ok'
            assert get_model_clients() is None
        assert clients.sync_client.is_closed and clients.async_client.is_closed
        assert budget.snapshot()['used_output_tokens'] == 3
    asyncio.run(run())


def test_normal_body_is_parsed_from_unread_wire_stream():
    budget = RequestBudget(max_calls=1, max_output_tokens=5)
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(lambda request:
            httpx.Response(200, stream=Chunks([json.dumps(completion()).encode()]))))) as client:
        with request_budget_scope(budget):
            client.post('https://offline.invalid', json={})
            assert budget.check() > 0
    assert budget.snapshot()['used_output_tokens'] == 5


def test_usage_without_raw_completion_tokens_does_not_release_reservation():
    payload = completion()
    payload['usage'] = {'prompt_tokens': 10, 'total_tokens': 10}
    budget = RequestBudget()
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(lambda request:
            httpx.Response(200, json=payload)))) as client:
        with request_budget_scope(budget):
            client.post('https://offline.invalid', json={})
    assert budget.snapshot()['used_output_tokens'] == 1024


def test_guard_error_during_iteration_closes_underlying_stream():
    stream = Chunks(events(finish='length'))
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(lambda request:
            httpx.Response(200, stream=stream, headers={'content-type': 'text/event-stream'})))) as client:
        with request_budget_scope(RequestBudget()), pytest.raises(BudgetExceeded):
            client.post('https://offline.invalid', json={})
    assert stream.closed


def test_late_response_close_detects_deadline_and_still_closes():
    now = [0]
    stream = Chunks(events())
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(lambda request:
            httpx.Response(200, stream=stream, headers={'content-type': 'text/event-stream'})))) as client:
        with request_budget_scope(RequestBudget(timeout_seconds=1, clock=lambda: now[0])):
            response = client.send(httpx.Request('POST', 'https://offline.invalid', json={}), stream=True)
            now[0] = 2
            with pytest.raises(BudgetExceeded, match='task_timeout'):
                response.close()
    assert stream.closed


def test_sse_without_done_is_conservative_even_after_usage():
    budget = RequestBudget()
    stream = Chunks(events()[:-3])
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(lambda request:
            httpx.Response(200, stream=stream, headers={'content-type': 'text/event-stream'})))) as client:
        with request_budget_scope(budget):
            client.post('https://offline.invalid', json={})
    assert budget.snapshot()['used_output_tokens'] == 1024


def test_request_scopes_are_independent_on_one_shared_client():
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(lambda request:
            httpx.Response(200, json=completion())))) as client:
        budgets = [RequestBudget(max_calls=1), RequestBudget(max_calls=1)]
        for budget in budgets:
            with request_budget_scope(budget):
                client.post('https://offline.invalid', json={})
        assert [b.snapshot()['used_calls'] for b in budgets] == [1, 1]
        assert not client.is_closed


def test_async_json_timeout_and_error_resource_closure():
    async def run():
        with request_budget_scope(RequestBudget()) as budget:
            async with httpx.AsyncClient(transport=AsyncBudgetTransport(httpx.MockTransport(
                lambda request: httpx.Response(200, stream=Chunks([json.dumps(completion()).encode()])),
            ))) as client:
                assert (await chat(async_client=client).ainvoke('hello')).content == 'ok'
            assert budget.snapshot()['used_output_tokens'] == 5
        stream = Chunks(events(finish='length'))
        async with httpx.AsyncClient(transport=AsyncBudgetTransport(httpx.MockTransport(lambda request:
                httpx.Response(200, stream=stream, headers={'content-type': 'text/event-stream'})))) as client:
            with request_budget_scope(RequestBudget()), pytest.raises(BudgetExceeded):
                await client.post('https://offline.invalid', json={})
        assert stream.closed
    asyncio.run(run())


@pytest.mark.parametrize('async_mode', [False, True])
def test_io_failure_after_deadline_reports_sticky_timeout(async_mode):
    now = [0]
    budget = RequestBudget(timeout_seconds=1, clock=lambda: now[0])
    def send(request):
        now[0] = 2
        raise httpx.ReadTimeout('offline timeout', request=request)
    async def run():
        async with httpx.AsyncClient(transport=AsyncBudgetTransport(httpx.MockTransport(send))) as client:
            with request_budget_scope(budget), pytest.raises(BudgetExceeded, match='task_timeout'):
                await client.post('https://offline.invalid', json={})
    if async_mode:
        asyncio.run(run())
    else:
        with httpx.Client(transport=BudgetTransport(httpx.MockTransport(send))) as client:
            with request_budget_scope(budget), pytest.raises(BudgetExceeded, match='task_timeout'):
                client.post('https://offline.invalid', json={})
    assert budget.snapshot()['used_calls'] == 1
    assert budget.snapshot()['used_output_tokens'] == 1024


def test_sse_usage_followed_by_length_keeps_conservative_charge():
    budget = RequestBudget()
    usage = b'data: {"usage":{"completion_tokens":3}}\n\n'
    length = b'data: {"choices":[{"finish_reason":"length"}]}\n\n'
    with httpx.Client(transport=BudgetTransport(httpx.MockTransport(lambda request:
            httpx.Response(200, stream=Chunks([usage, length]), headers={'content-type': 'text/event-stream'})))) as client:
        with request_budget_scope(budget), pytest.raises(BudgetExceeded, match='output_tokens_exhausted'):
            client.post('https://offline.invalid', json={})
    assert budget.snapshot()['used_output_tokens'] == 1024
