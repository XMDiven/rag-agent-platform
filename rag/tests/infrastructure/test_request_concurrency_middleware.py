import asyncio
import json
from types import SimpleNamespace

import pytest

from rag_app.infrastructure.request_budget import RequestBudget
from rag_app.infrastructure.request_budget_middleware import RequestBudgetMiddleware
from rag_app.infrastructure.request_concurrency import AdmissionRejected, RequestLimiter, admit_request


async def until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(.001)


def launch(app, limiter, *, timeout=1, send=None):
    incoming = asyncio.Queue()
    sent = []

    async def capture(message):
        sent.append(message)
        if send is not None:
            await send(message)

    def failure(code):
        return (json.dumps({'type': 'error', 'code': code})+'\n'+
                json.dumps({'type': 'done', 'status': 'failed'})+'\n').encode()

    middleware = RequestBudgetMiddleware(app, paths={'/ask'}, stream_error=failure,
        budget_factory=lambda: RequestBudget(timeout_seconds=timeout))
    task = asyncio.create_task(middleware({'type': 'http', 'method': 'POST', 'path': '/ask',
        'app': SimpleNamespace(state=SimpleNamespace(request_limiter=limiter))}, incoming.get, capture))
    return task, incoming, sent


def test_total_deadline_includes_queue_and_cancels_waiting_admission():
    async def run():
        limiter = RequestLimiter(max_active=1, max_waiting=1, wait_timeout_seconds=1)
        permit = await limiter.acquire()
        calls = []

        async def app(scope, receive, send):
            await admit_request()
            calls.append(1)

        task, _, sent = launch(app, limiter, timeout=.05)
        try:
            await asyncio.wait_for(task, 1)
            assert sent[0]['status'] == 504
            assert json.loads(sent[-1]['body'])['error']['code'] == 'task_timeout'
            await until(lambda: limiter.snapshot()['waiting'] == 0)
            assert calls == []
            assert limiter.snapshot()['active'] == 1
        finally:
            permit.release()
    asyncio.run(run())


def test_queued_disconnect_removes_waiter_without_starting_business():
    async def run():
        limiter = RequestLimiter(max_active=1, max_waiting=1)
        permit = await limiter.acquire()
        calls = []

        async def app(scope, receive, send):
            await admit_request()
            calls.append(1)

        task, incoming, sent = launch(app, limiter)
        try:
            await until(lambda: limiter.snapshot()['waiting'] == 1)
            await incoming.put({'type': 'http.disconnect'})
            await asyncio.wait_for(task, 1)
            await until(lambda: limiter.snapshot()['waiting'] == 0)
            assert calls == [] and sent == []
        finally:
            permit.release()
    asyncio.run(run())


def test_slot_covers_final_response_send_after_app_has_finished():
    async def run():
        limiter = RequestLimiter(max_active=1, max_waiting=0)
        sending, release = asyncio.Event(), asyncio.Event()

        async def app(scope, receive, send):
            await admit_request()
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'{}'})

        async def send(message):
            if message['type'] == 'http.response.body':
                sending.set()
                await release.wait()

        task, _, _ = launch(app, limiter, send=send)
        try:
            await asyncio.wait_for(sending.wait(), 1)
            with pytest.raises(AdmissionRejected, match='queue_full'):
                await limiter.acquire()
        finally:
            release.set()
            await task
        await until(lambda: limiter.snapshot()['active'] == 0)
    asyncio.run(run())


def test_slot_covers_cancelled_app_cleanup_after_http_response_ends():
    async def run():
        limiter = RequestLimiter(max_active=1, max_waiting=0)
        cleanup, release = asyncio.Event(), asyncio.Event()

        async def app(scope, receive, send):
            await admit_request()
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-type', b'application/x-ndjson')]})
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await release.wait()

        task, _, _ = launch(app, limiter, timeout=.05)
        try:
            await asyncio.wait_for(task, 1)
            await asyncio.wait_for(cleanup.wait(), 1)
            with pytest.raises(AdmissionRejected, match='queue_full'):
                await limiter.acquire()
        finally:
            release.set()
        await until(lambda: limiter.snapshot()['active'] == 0)
    asyncio.run(run())
