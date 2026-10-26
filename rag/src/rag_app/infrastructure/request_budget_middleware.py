"""Own a question's budget until its complete ASGI response has finished."""

import asyncio
import json
from time import monotonic
from contextlib import ExitStack, suppress
from typing import Callable

from rag_app.infrastructure.model_transport import model_clients_scope
from rag_app.infrastructure.request_budget import (
    BudgetExceeded, RequestBudget, request_budget_scope,
)


_pending_tasks: set[asyncio.Task] = set()
ERROR_DELIVERY_GRACE_SECONDS = 0.25


def configured_request_budget():
    from rag_app.config.config import settings

    return RequestBudget(
        timeout_seconds=settings.request_timeout_seconds,
        max_calls=settings.request_max_model_calls,
        max_output_tokens=settings.request_max_output_tokens,
        max_tokens_per_call=settings.request_max_tokens_per_call,
        max_context_bytes=settings.request_max_context_bytes,
        max_request_context_bytes=settings.request_max_context_bytes_per_call,
    )


def _release_task(task):
    _pending_tasks.discard(task)
    if not task.cancelled():
        task.exception()


def _cancel_task(task):
    # A sync worker can outlive its waiter. Do not wait indefinitely for it.
    if not task.done():
        if task in _pending_tasks:
            return
        _pending_tasks.add(task)
        task.add_done_callback(_release_task)
        task.cancel()
    elif not task.cancelled():
        task.exception()


class RequestBudgetMiddleware:
    def __init__(self, app, *, paths: set[str], stream_error: Callable[[str], bytes],
                 budget_factory: Callable[[], RequestBudget] | None = None):
        self.app = app
        self.paths = paths
        self.stream_error = stream_error
        self.budget_factory = budget_factory or configured_request_budget

    async def __call__(self, scope, receive, send):
        if (scope['type'] != 'http' or scope.get('method') != 'POST'
                or scope.get('path') not in self.paths):
            await self.app(scope, receive, send)
            return

        budget = self.budget_factory()
        disconnected = asyncio.Event()
        queue = asyncio.Queue(maxsize=1)
        active = True
        started = False
        streaming = False
        response_start = None
        ordinary_body = bytearray()
        tail = b''
        terminal = None
        send_lock = asyncio.Lock()

        async def app_forward(message, *, start=False):
            nonlocal started
            async with send_lock:
                if not active:
                    return
                budget.check()
                if start:
                    # Handing headers to send may already commit them.
                    started = True
                await send(message)

        async def pump_receive():
            # One raw reader; existing Starlette/Agent disconnect listeners use
            # the wrapper below. A bounded queue avoids pre-reading large bodies.
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    disconnected.set()
                    return
                await queue.put(message)

        async def app_receive():
            if disconnected.is_set():
                return {'type': 'http.disconnect'}
            item = asyncio.create_task(queue.get())
            gone = asyncio.create_task(disconnected.wait())
            try:
                done, _ = await asyncio.wait((item, gone), return_when=asyncio.FIRST_COMPLETED)
                if item in done:
                    return item.result()
                return {'type': 'http.disconnect'}
            finally:
                item.cancel()
                gone.cancel()
                await asyncio.gather(item, gone, return_exceptions=True)

        async def app_send(message):
            nonlocal started, streaming, response_start, tail, terminal
            if not active:
                return
            budget.check()  # Also catches a failure swallowed by a service.
            if message['type'] == 'http.response.start':
                response_start = message
                streaming = any(k.lower() == b'content-type' and b'application/x-ndjson' in v
                                for k, v in message.get('headers', []))
                if streaming:
                    # Starting send may already put headers on the wire even if
                    # it later blocks. Never attempt a second response.start.
                    await app_forward(message, start=True)
            elif message['type'] == 'http.response.body':
                if not streaming:
                    # These protected JSON endpoints have bounded answer/source
                    # payloads. Delay headers so late failures can replace success.
                    ordinary_body.extend(message.get('body', b''))
                    return
                tail += message.get('body', b'')
                while b'\n' in tail:
                    line, tail = tail.split(b'\n', 1)
                    if terminal is not None:
                        continue
                    if line and json.loads(line).get('type') == 'done':
                        terminal = line + b'\n'
                    else:
                        await app_forward({'type': 'http.response.body', 'body': line + b'\n', 'more_body': True})
                # Hold done until generator cleanup and the app itself complete.

        async def execute():
            with ExitStack() as stack:
                stack.enter_context(request_budget_scope(budget))
                app = scope.get('app')
                clients = getattr(getattr(app, 'state', None), 'model_clients', None)
                if clients is not None:
                    stack.enter_context(model_clients_scope(clients))
                await self.app(scope, app_receive, app_send)
                budget.check()

        receiver = asyncio.create_task(pump_receive())
        app_task = asyncio.create_task(execute())
        disconnect_task = asyncio.create_task(disconnected.wait())
        error = None

        async def deliver(message, remaining):
            """A slow client must not turn finalization into an unbounded wait."""
            if remaining <= 0 or disconnected.is_set():
                return False
            async def locked_send():
                nonlocal started
                async with send_lock:
                    if message['type'] == 'http.response.start':
                        started = True
                    await send(message)

            delivery = asyncio.create_task(locked_send())
            try:
                done, _ = await asyncio.wait((delivery, disconnect_task), timeout=remaining,
                                            return_when=asyncio.FIRST_COMPLETED)
                if delivery in done:
                    delivery.result()
                    return True
                budget.stop('request_cancelled' if disconnected.is_set() else 'task_timeout')
                return False
            finally:
                _cancel_task(delivery)

        async def send_failure(failure):
            nonlocal active
            active = False
            budget.stop(failure.code)
            _cancel_task(app_task)
            delivery_deadline = monotonic() + ERROR_DELIVERY_GRACE_SECONDS
            if started:
                if streaming:
                    await deliver({'type': 'http.response.body',
                                   'body': self.stream_error(failure.code), 'more_body': False},
                                  delivery_deadline - monotonic())
                return  # A started JSON response cannot be replaced safely.
            payload = json.dumps({'error': {'code': failure.code,
                                           'message': '请求超过执行预算，请缩小问题后重试。'}},
                                 ensure_ascii=False).encode()
            if await deliver({'type': 'http.response.start',
                              'status': 504 if failure.code == 'task_timeout' else 503,
                              'headers': [(b'content-type', b'application/json; charset=utf-8'),
                                          (b'content-length', str(len(payload)).encode())]},
                             delivery_deadline - monotonic()):
                await deliver({'type': 'http.response.body', 'body': payload},
                              delivery_deadline - monotonic())

        try:
            done, _ = await asyncio.wait(
                (app_task, disconnect_task), timeout=budget.check(),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnected.is_set():
                budget.stop('request_cancelled')
                return
            if app_task not in done:
                budget.stop('task_timeout')
            try:
                budget.check()
                app_task.result()
            except BaseException as exc:
                # The SDK may wrap BudgetExceeded in APIConnectionError or an
                # ExceptionGroup. The shared sticky budget remains authoritative.
                if isinstance(exc, BudgetExceeded):
                    budget.stop(exc.code)
                try:
                    budget.check()
                except BudgetExceeded as failure:
                    error = failure
                if error is None:
                    raise exc
            active = False
            if error is not None:
                await send_failure(error)
            elif streaming:
                if tail:
                    raise RuntimeError('incomplete NDJSON record')
                await deliver({'type': 'http.response.body', 'body': terminal or b'', 'more_body': False},
                              budget.check())
            elif response_start is not None:
                if await deliver(response_start, budget.check()):
                    await deliver({'type': 'http.response.body', 'body': bytes(ordinary_body)}, budget.check())
        except BudgetExceeded as failure:
            await send_failure(failure)
        except asyncio.CancelledError:
            budget.stop('request_cancelled')
            raise
        except OSError:
            budget.stop('request_cancelled')
            raise
        finally:
            active = False
            budget.stop('request_finished')
            _cancel_task(app_task)
            receiver.cancel()
            disconnect_task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(receiver, disconnect_task, return_exceptions=True)
