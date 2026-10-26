"""Guard actual model HTTP sends while retaining application-owned connection pools."""

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
from typing import Any

import httpx

from rag_app.infrastructure.request_budget import RequestBudget, get_request_budget


def _prepare(request: httpx.Request, body: bytes, budget: RequestBudget):
    payload = json.loads(body)
    lease = budget.reserve(payload)
    payload.pop('max_completion_tokens', None)
    payload['max_tokens'] = lease.allocation
    if payload.get('stream'):
        payload['stream_options'] = {**payload.get('stream_options', {}), 'include_usage': True}
    content = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    headers = request.headers.copy()
    headers['content-length'] = str(len(content))
    remaining = budget.check()
    extensions = request.extensions.copy()
    timeouts = extensions.get('timeout', {})
    extensions['timeout'] = {
        key: min(value, remaining) if isinstance(value, (int, float)) else remaining
        for key, value in ((key, timeouts.get(key)) for key in ('connect', 'read', 'write', 'pool'))
    }
    return httpx.Request(request.method, request.url, headers=headers, content=content,
                         extensions=extensions), lease


class _ResponseUsage:
    """Parse raw usage before SDK normalization; do not buffer or rewrite chunks."""
    def __init__(self, response: httpx.Response, budget: RequestBudget, lease):
        self.budget = budget
        self.lease = lease
        self.success = 200 <= response.status_code < 300
        self.sse = 'text/event-stream' in response.headers.get('content-type', '')
        self.buffer = b''
        self.completion_tokens: int | None = None
        self.done = False

    def _event(self, payload: Any):
        if not isinstance(payload, dict):
            return
        usage = payload.get('usage')
        if isinstance(usage, dict):
            value = usage.get('completion_tokens')
            if type(value) is int and value >= 0:
                self.completion_tokens = value
        if any(isinstance(choice, dict) and choice.get('finish_reason') == 'length'
               for choice in payload.get('choices', [])):
            self.budget.stop('output_tokens_exhausted')
            self.budget.check()

    def feed(self, chunk: bytes):
        if not self.success:
            return
        self.buffer += chunk
        if self.sse:
            self.buffer = self.buffer.replace(b'\r\n', b'\n')
            while b'\n\n' in self.buffer:
                event, self.buffer = self.buffer.split(b'\n\n', 1)
                data = b'\n'.join(line[5:].lstrip(b' ') for line in event.split(b'\n') if line.startswith(b'data:'))
                if data == b'[DONE]':
                    self.done = True
                    if self.completion_tokens is not None:
                        self.lease.settle(self.completion_tokens)
                else:
                    try:
                        self._event(json.loads(data))
                    except (ValueError, UnicodeError):
                        pass

    def finish(self):
        self.budget.check()
        if not self.success:
            return
        if not self.sse:
            try:
                self._event(json.loads(self.buffer))
            except (ValueError, UnicodeError):
                return
        # EOF alone does not prove that the terminal SSE usage event was received.
        if (not self.sse or self.done) and self.completion_tokens is not None:
            self.lease.settle(self.completion_tokens)
        self.budget.check()


class _SyncStream(httpx.SyncByteStream):
    def __init__(self, inner: httpx.Response, usage: _ResponseUsage):
        self.inner = inner
        self.usage = usage

    def __iter__(self):
        iterator = self.inner.iter_bytes()
        while True:
            self.usage.budget.check()
            try:
                chunk = next(iterator)
            except StopIteration:
                self.usage.finish()
                return
            except Exception:
                self.usage.budget.check()
                raise
            self.usage.budget.check()
            self.usage.feed(chunk)
            yield chunk

    def close(self):
        try:
            self.usage.budget.check()
        finally:
            try:
                self.inner.close()
            finally:
                self.usage.budget.check()


class _AsyncStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.Response, usage: _ResponseUsage):
        self.inner = inner
        self.usage = usage

    async def __aiter__(self):
        iterator = self.inner.aiter_bytes().__aiter__()
        while True:
            self.usage.budget.check()
            try:
                chunk = await iterator.__anext__()
            except StopAsyncIteration:
                self.usage.finish()
                return
            except Exception:
                self.usage.budget.check()
                raise
            self.usage.budget.check()
            self.usage.feed(chunk)
            yield chunk

    async def aclose(self):
        try:
            self.usage.budget.check()
        finally:
            try:
                await self.inner.aclose()
            finally:
                self.usage.budget.check()


class BudgetTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport):
        self.inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        budget = get_request_budget()
        if budget is None:
            return self.inner.handle_request(request)
        budget.check()
        request, lease = _prepare(request, request.read(), budget)
        try:
            response = self.inner.handle_request(request)
        except Exception:
            budget.check()
            raise
        try:
            budget.check()
        except BaseException:
            response.close()
            raise
        usage = _ResponseUsage(response, budget, lease)
        if response.is_stream_consumed:
            usage.feed(response.content)
            usage.finish()
            return response
        decoded = _decoded_response(response)
        response.stream = _SyncStream(decoded, usage)
        return response

    def close(self):
        self.inner.close()


class AsyncBudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport):
        self.inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        budget = get_request_budget()
        if budget is None:
            return await self.inner.handle_async_request(request)
        budget.check()
        request, lease = _prepare(request, await request.aread(), budget)
        try:
            response = await self.inner.handle_async_request(request)
        except Exception:
            budget.check()
            raise
        try:
            budget.check()
        except BaseException:
            await response.aclose()
            raise
        usage = _ResponseUsage(response, budget, lease)
        if response.is_stream_consumed:
            usage.feed(response.content)
            usage.finish()
            return response
        decoded = _decoded_response(response)
        response.stream = _AsyncStream(decoded, usage)
        return response

    async def aclose(self):
        await self.inner.aclose()


def _decoded_response(response: httpx.Response) -> httpx.Response:
    # Use httpx's public streaming decoder for gzip/deflate/etc., before usage
    # accounting. The outer response now carries decoded bytes, so prevent a
    # second decode. No complete stream buffering or private decoder API.
    decoded = httpx.Response(response.status_code, headers=response.headers, stream=response.stream)
    if 'content-encoding' in response.headers:
        del response.headers['content-encoding']
        response.headers.pop('content-length', None)
    return decoded


@dataclass(frozen=True)
class ModelClients:
    sync_client: httpx.Client
    async_client: httpx.AsyncClient


_active_model_clients: ContextVar[ModelClients | None] = ContextVar('model_clients', default=None)


def get_model_clients() -> ModelClients | None:
    return _active_model_clients.get()


@contextmanager
def model_clients_scope(clients: ModelClients):
    token = _active_model_clients.set(clients)
    try:
        yield clients
    finally:
        _active_model_clients.reset(token)


@asynccontextmanager
async def model_clients(*, sync_transport=None, async_transport=None):
    sync_client = httpx.Client(transport=BudgetTransport(
        sync_transport if sync_transport is not None else httpx.HTTPTransport(retries=0, trust_env=False)),
        trust_env=False)
    try:
        async_client = httpx.AsyncClient(transport=AsyncBudgetTransport(
            async_transport if async_transport is not None else httpx.AsyncHTTPTransport(retries=0, trust_env=False)),
            trust_env=False)
        try:
            yield ModelClients(sync_client, async_client)
        finally:
            await async_client.aclose()
    finally:
        sync_client.close()
