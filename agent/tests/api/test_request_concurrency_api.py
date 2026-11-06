import asyncio
import json
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from agent_app.app import main as agent_main
from agent_app.app.routers import run as agent_router
from rag_app.app import main as rag_main
from rag_app.app.routers import ask as rag_router
from rag_app.config import config
from rag_app.config.config import Settings
from rag_app.infrastructure.resources import AppResources


PATHS = [('/ask', rag_main), ('/ask/stream', rag_main),
         ('/agent/run', agent_main), ('/agent/run/stream', agent_main)]


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


def configure(monkeypatch, *, active=1, waiting=1, queue_timeout=.2, timeout=5):
    monkeypatch.setattr(config, 'settings', Settings(
        _env_file=None, request_max_concurrent=active, request_max_waiting=waiting,
        request_queue_timeout_seconds=queue_timeout, request_timeout_seconds=timeout,
    ))
    monkeypatch.setattr(rag_main, 'create_app_resources', lambda: AppResources(Mock(), Mock()))


def install_business(monkeypatch, path, block):
    def ask(*args, **kwargs):
        block()
        return {'answer': 'offline', 'sources': [], 'trace': []}

    def agent(*args, **kwargs):
        block()
        return SimpleNamespace(
            plan=SimpleNamespace(tool=SimpleNamespace(name='fallback_tool')),
            tool_result=SimpleNamespace(output={'answer': 'offline', 'sources': []}, status='success'),
            trace=[], steps=[], termination_reason='final_answer',
        )

    def rag_stream(*args, **kwargs):
        block()
        yield {'type': 'answer_delta', 'content': 'offline'}
        yield {'type': 'done'}

    async def agent_stream(question):
        # The actual Agent tool path is tested separately. This stream checks
        # route admission without a blocking operation on the event loop.
        from rag_app.infrastructure.tracked_work import run_sync_tracked
        await run_sync_tracked(block)
        yield json.dumps({'version': 1, 'type': 'answer_delta', 'data': {'text': 'offline'}}) + '\n'
        yield json.dumps({'version': 1, 'type': 'done', 'data': {
            'termination_reason': 'final_answer', 'selected_tool': 'fallback_tool', 'tool_status': 'success',
        }}) + '\n'

    monkeypatch.setattr(rag_router, 'ask_question', ask)
    monkeypatch.setattr(agent_router, 'run_agent', agent)
    monkeypatch.setattr(rag_router, 'stream_ask_question', rag_stream)
    monkeypatch.setattr(agent_router, 'stream_agent_ndjson', agent_stream)


@pytest.mark.parametrize('path,module', PATHS)
@pytest.mark.parametrize('waiting,code', [(0, 'queue_full'), (1, 'queue_timeout')])
def test_rejects_before_stream_headers_without_starting_business(monkeypatch, path, module, waiting, code):
    configure(monkeypatch, waiting=waiting, queue_timeout=.04)
    entered, release = Event(), Event()
    calls = []

    def block():
        calls.append(1)
        entered.set()
        assert release.wait(3)

    install_business(monkeypatch, path, block)

    async def run():
        async with module.lifespan(module.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://offline') as client:
                first = asyncio.create_task(client.post(path, json={'question': '问题'}))
                try:
                    await until(entered.is_set)
                    limiter = module.app.state.request_limiter
                    assert limiter.snapshot()['active'] == 1
                    invalid = await client.post(path, json={})
                    assert invalid.status_code == 422
                    rejected = await client.post(path, json={'question': '等待的问题'}, headers={
                        'x-request-id': 'queue-test', 'origin': 'http://localhost:3000',
                    })
                    assert rejected.status_code == 503
                    assert rejected.headers['content-type'].startswith('application/json')
                    assert rejected.json()['error']['code'] == code
                    assert rejected.headers['x-request-id'] == 'queue-test'
                    assert rejected.headers['access-control-allow-origin'] == 'http://localhost:3000'
                    assert len(calls) == 1
                    assert limiter.snapshot()['waiting'] == 0
                finally:
                    release.set()
                    response = await first
                assert response.status_code == 200
                await until(lambda: limiter.snapshot()['active'] == 0)
    asyncio.run(run())


@pytest.mark.parametrize('path,module', PATHS)
def test_twenty_questions_never_exceed_four_actual_workers(monkeypatch, path, module):
    configure(monkeypatch, active=4, waiting=16, queue_timeout=2)
    release, lock = Event(), Lock()
    active, maximum, calls = 0, 0, 0

    def block():
        nonlocal active, maximum, calls
        with lock:
            active += 1
            calls += 1
            maximum = max(maximum, active)
        try:
            assert release.wait(3)
        finally:
            with lock:
                active -= 1

    install_business(monkeypatch, path, block)

    async def run():
        async with module.lifespan(module.app):
            limiter = module.app.state.request_limiter
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://offline') as client:
                requests = [asyncio.create_task(client.post(path, json={'question': '问题'})) for _ in range(20)]
                try:
                    await until(lambda: calls == 4 and limiter.snapshot()['waiting'] == 16)
                    assert limiter.snapshot()['active'] == 4
                finally:
                    release.set()
                    responses = await asyncio.gather(*requests)
                assert all(response.status_code == 200 for response in responses)
                await until(lambda: limiter.snapshot()['active'] == 0)
                assert limiter.snapshot()['waiting'] == 0
    asyncio.run(run())
    assert calls == 20 and maximum <= 4 and active == 0


@pytest.mark.parametrize('path,module', PATHS)
def test_timed_out_response_keeps_slot_until_actual_thread_finishes(monkeypatch, path, module):
    configure(monkeypatch, waiting=0, timeout=.15)
    entered, release, exited = Event(), Event(), Event()
    calls = []

    def block():
        calls.append(1)
        entered.set()
        try:
            assert release.wait(3)
        finally:
            exited.set()

    install_business(monkeypatch, path, block)

    async def run():
        async with module.lifespan(module.app):
            limiter = module.app.state.request_limiter
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://offline') as client:
                try:
                    response = await client.post(path, json={'question': '问题'})
                    assert entered.is_set()
                    if path.endswith('/stream'):
                        events = [json.loads(line) for line in response.text.splitlines()]
                        assert events[-1]['type'] == 'done'
                        assert events[-2].get('data', events[-2])['code'] == 'task_timeout'
                    else:
                        assert response.status_code == 504
                    assert limiter.snapshot()['active'] == 1
                    second = await client.post(path, json={'question': '不能插入'})
                    assert second.status_code == 503
                    assert second.json()['error']['code'] == 'queue_full'
                    assert len(calls) == 1
                finally:
                    release.set()
                await until(lambda: exited.is_set() and limiter.snapshot()['active'] == 0)
                third = await client.post(path, json={'question': '可以执行'})
                assert third.status_code == 200
    asyncio.run(run())


def test_actual_agent_tool_inherits_ownership_and_does_not_start_next_round(monkeypatch):
    from langchain_core.messages import AIMessageChunk
    from agent_app.orchestration import executor
    from agent_app import streaming_service
    from rag_app.infrastructure.request_concurrency import get_request_work
    configure(monkeypatch, waiting=0, timeout=.15)
    release, entered = Event(), Event()
    rounds, work_records = [], []

    class Model:
        def bind_tools(self, *args, **kwargs):
            return self

        async def astream(self, messages):
            rounds.append(1)
            work_records.append(get_request_work())
            yield AIMessageChunk(content='', tool_call_chunks=[{
                'name': 'retrieval_tool', 'args': '{"question":"问题"}', 'id': 'a', 'index': 0,
            }])

    def retrieval(question):
        work_records.append(get_request_work())
        entered.set()
        assert release.wait(3)
        return {'answer': 'late', 'sources': []}

    monkeypatch.setattr(streaming_service, 'get_client', lambda: Model())
    monkeypatch.setattr(executor, 'run_retrieval_tool', retrieval)

    async def run():
        async with agent_main.lifespan(agent_main.app):
            limiter = agent_main.app.state.request_limiter
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent_main.app), base_url='http://offline') as client:
                try:
                    response = await client.post('/agent/run/stream', json={'question': '问题'})
                    assert entered.is_set()
                    events = [json.loads(line) for line in response.text.splitlines()]
                    assert events[-1]['data']['termination_reason'] == 'failed'
                    assert limiter.snapshot()['active'] == 1
                    assert len(work_records) == 2 and work_records[0] is work_records[1]
                    assert work_records[0] is not None
                finally:
                    release.set()
                await until(lambda: limiter.snapshot()['active'] == 0)
    asyncio.run(run())
    assert len(rounds) == 1


@pytest.mark.parametrize('module', [rag_main, agent_main])
def test_lifespan_closes_admission_and_restarts_with_independent_limiter(monkeypatch, module):
    from rag_app.infrastructure.request_concurrency import AdmissionRejected, RequestWork
    configure(monkeypatch)
    install_business(monkeypatch, '/ask', Mock(side_effect=AssertionError('closed application must not execute')))

    async def run():
        async with module.lifespan(module.app):
            old = module.app.state.request_limiter
            work = RequestWork(old)
            await work.admit()
            ticket = work.register_worker()
            ticket.begin()
            work.app_finished()
            work.response_finished()
        assert old.snapshot()['closed']
        with pytest.raises(AdmissionRejected, match='service_unavailable'):
            await old.acquire()
        path = '/ask/stream' if module is rag_main else '/agent/run/stream'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://offline') as client:
            rejected = await client.post(path, json={'question': '服务已关闭'})
            assert rejected.status_code == 503
            assert rejected.json()['error']['code'] == 'service_unavailable'
        async with module.lifespan(module.app):
            current = module.app.state.request_limiter
            assert current is not old
            permit = await current.acquire()
            ticket.finish()
            await until(lambda: old.snapshot()['active'] == 0)
            assert current.snapshot()['active'] == 1
            permit.release()
    asyncio.run(run())
