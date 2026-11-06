import asyncio

import pytest

from rag_app.infrastructure.request_budget import BudgetExceeded, RequestBudget, request_budget_scope
from rag_app.infrastructure.request_concurrency import (
    AdmissionRejected, RequestLimiter, RequestWork, admit_request, get_request_work, request_work_scope,
)


def test_capacity_waiting_overflow_and_release_once():
    async def scenario():
        limiter = RequestLimiter()
        permits = [await limiter.acquire() for _ in range(4)]
        queued = [asyncio.create_task(limiter.acquire()) for _ in range(16)]
        await asyncio.sleep(0)
        assert limiter.snapshot() == {'active': 4, 'waiting': 16, 'closed': False}
        with pytest.raises(AdmissionRejected, match='queue_full'):
            await limiter.acquire()
        for permit in permits:
            permit.release()
            permit.release()
        for task in queued:
            (await task).release()
        assert limiter.snapshot()['active'] == limiter.snapshot()['waiting'] == 0
    asyncio.run(scenario())


def test_timeout_cancel_handoff_close_and_zero_waiting():
    async def scenario():
        limiter = RequestLimiter(max_active=1, max_waiting=2, wait_timeout_seconds=.01)
        first = await limiter.acquire()
        with pytest.raises(AdmissionRejected, match='queue_timeout'):
            await limiter.acquire()
        queued = asyncio.create_task(limiter.acquire())
        await asyncio.sleep(0)
        first.release()
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        next_permit = await limiter.acquire()
        waiting = asyncio.create_task(limiter.acquire())
        await asyncio.sleep(0)
        limiter.close()
        with pytest.raises(AdmissionRejected, match='service_unavailable'):
            await waiting
        next_permit.release()
        with pytest.raises(AdmissionRejected, match='service_unavailable'):
            await limiter.acquire()
        assert limiter.snapshot() == {'active': 0, 'waiting': 0, 'closed': True}
        no_queue = RequestLimiter(max_active=1, max_waiting=0)
        permit = await no_queue.acquire()
        with pytest.raises(AdmissionRejected, match='queue_full'):
            await no_queue.acquire()
        permit.release()
        (await no_queue.acquire()).release()
    asyncio.run(scenario())


def test_request_lifecycle_context_and_sticky_admission_failure():
    async def scenario():
        await admit_request()
        assert get_request_work() is None
        limiter = RequestLimiter(max_active=1, max_waiting=0)
        work = RequestWork(limiter)
        with request_work_scope(work):
            await admit_request()
            await admit_request()
        assert limiter.snapshot()['active'] == 1
        work.app_finished()
        work.app_finished()
        assert limiter.snapshot()['active'] == 1
        work.response_finished()
        work.response_finished()
        assert limiter.snapshot()['active'] == 0
        permit = await limiter.acquire()
        budget = RequestBudget()
        with request_budget_scope(budget), request_work_scope(RequestWork(limiter)):
            with pytest.raises(AdmissionRejected, match='queue_full'):
                await admit_request()
            permit.release()
            with pytest.raises(BudgetExceeded, match='queue_full'):
                await admit_request()
    asyncio.run(scenario())


def test_stopped_budget_after_wait_releases_acquired_permit():
    async def scenario():
        limiter = RequestLimiter(max_active=1)
        permit = await limiter.acquire()
        budget = RequestBudget()
        with request_budget_scope(budget):
            task = asyncio.create_task(RequestWork(limiter).admit())
        await asyncio.sleep(0)
        budget.stop('request_cancelled')
        permit.release()
        with pytest.raises(BudgetExceeded, match='request_cancelled'):
            await task
        assert limiter.snapshot()['active'] == 0
    asyncio.run(scenario())


@pytest.mark.parametrize('kwargs', [{'max_active': 0}, {'max_waiting': -1}, {'wait_timeout_seconds': 0}])
def test_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        RequestLimiter(**kwargs)


def test_expired_request_budget_takes_priority_over_queue_timeout():
    async def scenario():
        limiter = RequestLimiter(max_active=1, wait_timeout_seconds=.02)
        permit = await limiter.acquire()
        now = [0.0]
        budget = RequestBudget(timeout_seconds=1, clock=lambda: now[0])
        try:
            with request_budget_scope(budget):
                queued = asyncio.create_task(RequestWork(limiter).admit())
            await asyncio.sleep(0)
            now[0] = 2
            with pytest.raises(BudgetExceeded, match='task_timeout'):
                await queued
        finally:
            permit.release()
        assert limiter.snapshot()['waiting'] == 0
    asyncio.run(scenario())
