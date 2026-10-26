import asyncio
import json
from threading import Event

import anyio
import pytest

from rag_app.infrastructure.request_budget import (
    BudgetExceeded, RequestBudget, get_request_budget,
)
from rag_app.infrastructure.request_budget_middleware import RequestBudgetMiddleware


def encode_failure(code):
    return (json.dumps({"type": "error", "code": code}) + '\n' +
            json.dumps({"type": "done", "status": "failed"}) + '\n').encode()


async def drive(app, *, timeout=.15, path="/ask", receive=None, budget_factory=None):
    sent = []
    blocked = asyncio.Event()

    async def default_receive():
        await blocked.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    middleware = RequestBudgetMiddleware(
        app, paths={"/ask", "/ask/stream"}, stream_error=encode_failure,
        budget_factory=budget_factory or (lambda: RequestBudget(timeout_seconds=timeout)),
    )
    await asyncio.wait_for(middleware(
        {"type": "http", "method": "POST", "path": path, "state": {}},
        receive or default_receive, send,
    ), 1)
    assert get_request_budget() is None
    return sent


def body(sent):
    return b''.join(m.get("body", b'') for m in sent)


def test_deadline_before_headers_and_swallowed_failure():
    async def never(scope, receive, send):
        await asyncio.Event().wait()

    async def swallowed(scope, receive, send):
        get_request_budget().stop("model_calls_exhausted")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"answer":"wrong"}'})

    for app, status, code in [(never, 504, "task_timeout"),
                               (swallowed, 503, "model_calls_exhausted")]:
        sent = asyncio.run(drive(app))
        assert sent[0]["status"] == status
        assert json.loads(body(sent))["error"]["code"] == code


def test_explicit_budget_exception_is_a_budget_response():
    async def app(scope, receive, send):
        raise BudgetExceeded('model_calls_exhausted')

    sent = asyncio.run(drive(app))
    assert sent[0]['status'] == 503
    assert json.loads(body(sent))['error']['code'] == 'model_calls_exhausted'


@pytest.mark.parametrize("partial", [b'', b'{"type":"answer_', '中文'.encode()[:1]])
def test_stream_deadline_discards_incomplete_record_and_emits_one_failed_done(partial):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b'content-type', b'application/x-ndjson')]})
        await send({"type": "http.response.body", "body": partial, "more_body": True})
        await asyncio.Event().wait()

    sent = asyncio.run(drive(app, path="/ask/stream"))
    events = [json.loads(line) for line in body(sent).splitlines()]
    assert [e["type"] for e in events] == ["error", "done"]
    assert events[-1]["status"] == "failed"


def test_success_done_is_held_until_cleanup_finishes():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b'content-type', b'application/x-ndjson')]})
        await send({"type": "http.response.body", "body": b'{"type":"done"}\n', "more_body": True})
        await asyncio.Event().wait()

    events = [json.loads(line) for line in body(asyncio.run(drive(app))).splitlines()]
    assert [e["type"] for e in events] == ["error", "done"]
    assert events[-1]["status"] == "failed"


def test_sync_worker_can_finish_but_cannot_start_more_work_after_deadline():
    entered, release, finished = Event(), Event(), Event()
    outcomes = []

    def worker():
        entered.set()
        release.wait(1)
        try:
            get_request_budget().check()
            outcomes.append("unexpected followup")
        except BudgetExceeded as error:
            outcomes.append(error.code)
        finally:
            finished.set()

    async def app(scope, receive, send):
        await anyio.to_thread.run_sync(worker, abandon_on_cancel=True)

    try:
        sent = asyncio.run(drive(app))
        assert entered.is_set() and sent[0]["status"] == 504
    finally:
        release.set()
    assert finished.wait(1)
    assert outcomes == ["task_timeout"]


def test_concurrent_scopes_are_independent_and_reset():
    seen = []

    async def app(scope, receive, send):
        budget = get_request_budget()
        seen.append(budget)
        await asyncio.sleep(.005)
        assert get_request_budget() is budget
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{}'})

    async def run():
        await asyncio.gather(drive(app), drive(app))

    asyncio.run(run())
    assert len(seen) == 2 and seen[0] is not seen[1]


def test_disconnect_stops_budget_and_returns_without_response():
    seen = []

    async def app(scope, receive, send):
        seen.append(get_request_budget())
        await asyncio.Event().wait()

    async def receive():
        await asyncio.sleep(.005)
        return {"type": "http.disconnect"}

    assert asyncio.run(drive(app, receive=receive)) == []
    with pytest.raises(BudgetExceeded, match="request_cancelled"):
        seen[0].check()


@pytest.mark.parametrize('streaming', [False, True])
def test_slow_client_cannot_hold_final_or_error_send_forever(streaming):
    async def run():
        blocked = asyncio.Event()
        attempts = []

        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers':
                        [(b'content-type', b'application/x-ndjson')] if streaming else []})
            await send({'type': 'http.response.body', 'body': b'{"type":"done"}\n' if streaming else b'{}'})

        async def receive():
            await blocked.wait()

        async def send(message):
            attempts.append(message)
            await blocked.wait()

        middleware = RequestBudgetMiddleware(app, paths={'/ask'}, stream_error=encode_failure,
            budget_factory=lambda: RequestBudget(timeout_seconds=.02))
        await asyncio.wait_for(middleware({'type': 'http', 'method': 'POST', 'path': '/ask'}, receive, send), .7)
        assert sum(m['type'] == 'http.response.start' for m in attempts) == 1

    asyncio.run(run())


@pytest.mark.parametrize('resists_cancellation', [False, True])
def test_error_delivery_never_overlaps_cancelled_send_cleanup(resists_cancellation):
    async def run():
        blocked, release = asyncio.Event(), asyncio.Event()
        active, maximum = 0, 0

        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-type', b'application/x-ndjson')]})
            await send({'type': 'http.response.body', 'body': b'{"type":"answer_delta"}\n', 'more_body': True})

        async def receive():
            await blocked.wait()

        async def send(message):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                if message.get('more_body'):
                    try:
                        await blocked.wait()
                    except asyncio.CancelledError:
                        if resists_cancellation:
                            await release.wait()
                        else:
                            raise
            finally:
                await asyncio.sleep(.01)
                active -= 1

        middleware = RequestBudgetMiddleware(app, paths={'/ask'}, stream_error=encode_failure,
            budget_factory=lambda: RequestBudget(timeout_seconds=.04))
        try:
            await asyncio.wait_for(middleware({'type': 'http', 'method': 'POST', 'path': '/ask'}, receive, send), .7)
        finally:
            release.set()
            await asyncio.sleep(.03)
        assert maximum == 1

    asyncio.run(run())


@pytest.mark.parametrize('phase', ['initial', 'json_final', 'stream_final'])
def test_deadline_at_supervisor_or_final_send_boundary_uses_failure_protocol(phase):
    reads = 0
    expires_at = {'initial': 2, 'json_final': 7, 'stream_final': 8}[phase]

    def clock():
        nonlocal reads
        reads += 1
        return 91 if reads >= expires_at else 0

    async def app(scope, receive, send):
        headers = [(b'content-type', b'application/x-ndjson')] if phase == 'stream_final' else []
        await send({'type': 'http.response.start', 'status': 200, 'headers': headers})
        await send({'type': 'http.response.body', 'body': b'{"type":"done"}\n'})

    sent = asyncio.run(drive(app, budget_factory=lambda: RequestBudget(timeout_seconds=90, clock=clock)))
    if phase == 'stream_final':
        assert [json.loads(row)['type'] for row in body(sent).splitlines()] == ['error', 'done']
    else:
        assert sent[0]['status'] == 504
        assert json.loads(body(sent))['error']['code'] == 'task_timeout'
