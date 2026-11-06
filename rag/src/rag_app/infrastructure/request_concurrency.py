"""Process-local admission and ownership of a request's actual work."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock

from rag_app.infrastructure.request_budget import (
    BudgetExceeded, check_request_budget, get_request_budget,
)


class AdmissionRejected(BudgetExceeded):
    pass


class Permit:
    def __init__(self, limiter: 'RequestLimiter'):
        self._limiter = limiter
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._limiter._active -= 1
            self._limiter._semaphore.release()


class RequestLimiter:
    def __init__(self, max_active: int = 4, max_waiting: int = 16,
                 wait_timeout_seconds: float = 5):
        if max_active <= 0 or max_waiting < 0 or wait_timeout_seconds <= 0:
            raise ValueError('invalid request concurrency limits')
        self._semaphore = asyncio.Semaphore(max_active)
        self._max_waiting = max_waiting
        self._wait_timeout_seconds = wait_timeout_seconds
        self._active = 0
        self._waiting = 0
        self._closed = False

    async def acquire(self) -> Permit:
        if self._closed:
            raise AdmissionRejected('service_unavailable')
        queued = self._semaphore.locked()
        if queued and self._waiting >= self._max_waiting:
            raise AdmissionRejected('queue_full')
        if queued:
            self._waiting += 1
        try:
            # Keep acquire in this task: cancelling a separate acquire task can
            # orphan a token during the handoff from a releasing request.
            async with asyncio.timeout(self._wait_timeout_seconds):
                await self._semaphore.acquire()
        except TimeoutError:
            raise AdmissionRejected('queue_timeout') from None
        finally:
            if queued:
                self._waiting -= 1
        if self._closed:
            self._semaphore.release()
            raise AdmissionRejected('service_unavailable')
        self._active += 1
        return Permit(self)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            for _ in range(self._waiting):
                self._semaphore.release()

    def snapshot(self) -> dict:
        return {'active': self._active, 'waiting': self._waiting, 'closed': self._closed}


class _WorkerTicket:
    def __init__(self, work: 'RequestWork'):
        self._work = work
        self._started = False
        self._finished = False

    def begin(self) -> bool:
        with self._work._lock:
            if self._finished:
                return False
            self._started = True
            return True

    def finish(self) -> None:
        self._finish(only_unstarted=False)

    def withdraw_if_not_started(self) -> None:
        self._finish(only_unstarted=True)

    def _finish(self, *, only_unstarted: bool) -> None:
        with self._work._lock:
            if self._finished or (only_unstarted and self._started):
                return
            self._finished = True
            self._work._workers -= 1
        self._work._notify_completion()


class RequestWork:
    def __init__(self, limiter: RequestLimiter):
        self._limiter = limiter
        self._loop = asyncio.get_running_loop()
        self._lock = RLock()
        self._admit_lock = asyncio.Lock()
        self._permit: Permit | None = None
        self._workers = 0
        self._app_finished = False
        self._response_finished = False

    async def admit(self) -> None:
        async with self._admit_lock:
            check_request_budget()
            if self._permit is not None:
                return
            try:
                permit = await self._limiter.acquire()
            except AdmissionRejected as exc:
                budget = get_request_budget()
                if budget is not None:
                    # A deadline reached while queued takes priority even if
                    # no other coroutine has observed it yet.
                    budget.check()
                    budget.stop(exc.code)
                raise
            try:
                check_request_budget()
            except BaseException:
                permit.release()
                raise
            with self._lock:
                self._permit = permit
            self._maybe_release()

    def register_worker(self) -> _WorkerTicket:
        with self._lock:
            self._workers += 1
        return _WorkerTicket(self)

    def app_finished(self) -> None:
        with self._lock:
            self._app_finished = True
        self._maybe_release()

    def response_finished(self) -> None:
        with self._lock:
            self._response_finished = True
        self._maybe_release()

    def _notify_completion(self) -> None:
        try:
            self._loop.call_soon_threadsafe(self._maybe_release)
        except RuntimeError:
            # The worker can finish after the server's event loop shuts down.
            pass

    def _maybe_release(self) -> None:
        with self._lock:
            if self._app_finished and self._response_finished and self._workers == 0:
                if self._permit is not None:
                    self._permit.release()

    def snapshot(self) -> dict:
        with self._lock:
            return {'workers': self._workers, 'app_finished': self._app_finished,
                    'response_finished': self._response_finished}


_active_work: ContextVar[RequestWork | None] = ContextVar('request_work', default=None)


def get_request_work() -> RequestWork | None:
    return _active_work.get()


@contextmanager
def request_work_scope(work: RequestWork):
    token = _active_work.set(work)
    try:
        yield work
    finally:
        _active_work.reset(token)


async def admit_request() -> None:
    work = get_request_work()
    if work is not None:
        await work.admit()
