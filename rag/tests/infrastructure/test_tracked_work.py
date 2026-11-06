import asyncio
from contextvars import ContextVar
from threading import Event, get_ident

import anyio
import pytest

from rag_app.infrastructure.request_budget import BudgetExceeded, RequestBudget, request_budget_scope
from rag_app.infrastructure.request_concurrency import RequestLimiter, RequestWork, request_work_scope
from rag_app.infrastructure.tracked_work import iterate_sync_tracked, run_sync_tracked


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


def test_cancelled_waiter_holds_permit_until_actual_worker_finishes():
    async def scenario():
        limiter = RequestLimiter(max_active=1)
        work = RequestWork(limiter)
        started, release, exited = Event(), Event(), Event()
        def worker():
            started.set()
            try:
                release.wait(2)
            finally:
                exited.set()
        with request_work_scope(work):
            await work.admit()
            task = asyncio.create_task(run_sync_tracked(worker))
        try:
            await wait_until(started.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            work.app_finished()
            work.response_finished()
            assert limiter.snapshot()['active'] == 1
            assert work.snapshot()['workers'] == 1
        finally:
            release.set()
        await wait_until(exited.is_set)
        await wait_until(lambda: limiter.snapshot()['active'] == 0)
    asyncio.run(scenario())


def test_prestart_cancel_retains_ticket_and_budget_prevents_late_business():
    async def scenario():
        limiter = RequestLimiter(max_active=1)
        work, budget = RequestWork(limiter), RequestBudget()
        called = []
        capacity = anyio.to_thread.current_default_thread_limiter()
        old_capacity = capacity.total_tokens
        capacity.total_tokens = 1
        await capacity.acquire()
        try:
            with request_work_scope(work), request_budget_scope(budget):
                await work.admit()
                task = asyncio.create_task(run_sync_tracked(lambda: called.append(True)))
            await wait_until(lambda: work.snapshot()['workers'] == 1)
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            budget.stop('task_timeout')
            work.app_finished()
            work.response_finished()
            assert limiter.snapshot()['active'] == 1
        finally:
            capacity.release()
            capacity.total_tokens = old_capacity
        await wait_until(lambda: limiter.snapshot()['active'] == 0)
        assert called == []
    asyncio.run(scenario())


def test_context_result_exceptions_and_no_scope_behavior():
    async def scenario():
        marker = ContextVar('tracked_test', default='missing')
        marker.set('present')
        assert await run_sync_tracked(lambda value: (marker.get(), value), value=3) == ('present', 3)
        work = RequestWork(RequestLimiter())
        with request_work_scope(work):
            assert await run_sync_tracked(lambda: marker.get()) == 'present'
            with pytest.raises(ValueError, match='worker failure'):
                await run_sync_tracked(lambda: (_ for _ in ()).throw(ValueError('worker failure')))
        assert work.snapshot()['workers'] == 0
    asyncio.run(scenario())


def test_iterator_is_incremental_bounded_and_propagates_error():
    async def scenario():
        generated = []
        def source():
            for item in range(10):
                generated.append(item)
                yield item
            raise ValueError('source failure')
        stream = iterate_sync_tracked(source())
        assert await anext(stream) == 0
        await asyncio.sleep(.01)
        assert len(generated) <= 3
        received = []
        with pytest.raises(ValueError, match='source failure'):
            async for item in stream:
                received.append(item)
        assert received == list(range(1, 10))
    asyncio.run(scenario())


def test_iterator_cancel_during_next_keeps_permit_and_closes_on_same_thread():
    async def scenario():
        limiter = RequestLimiter(max_active=1)
        work = RequestWork(limiter)
        entered, unblock, closed = Event(), Event(), Event()
        thread_ids, next_items = [], []
        def source():
            thread_ids.append(get_ident())
            try:
                entered.set()
                unblock.wait(2)
                next_items.append(1)
                yield 1
                next_items.append(2)
                yield 2
            finally:
                thread_ids.append(get_ident())
                closed.set()
        with request_work_scope(work):
            await work.admit()
            stream = iterate_sync_tracked(source())
            first = asyncio.create_task(anext(stream))
        try:
            await wait_until(entered.is_set)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            work.app_finished()
            work.response_finished()
            assert limiter.snapshot()['active'] == 1
            assert not closed.is_set()
        finally:
            unblock.set()
        await wait_until(closed.is_set)
        await wait_until(lambda: limiter.snapshot()['active'] == 0)
        assert next_items == [1]
        assert len(thread_ids) == 2 and thread_ids[0] == thread_ids[1]
    asyncio.run(scenario())


def test_iterator_early_close_unblocks_full_channel_and_releases_worker():
    async def scenario():
        work = RequestWork(RequestLimiter())
        closed = Event()
        def source():
            try:
                yield from range(100)
            finally:
                closed.set()
        with request_work_scope(work):
            stream = iterate_sync_tracked(source())
            assert await anext(stream) == 0
        await asyncio.sleep(.01)
        await stream.aclose()
        await wait_until(closed.is_set)
        await wait_until(lambda: work.snapshot()['workers'] == 0)
    asyncio.run(scenario())


def test_iterator_no_scope_completes():
    async def scenario():
        assert [item async for item in iterate_sync_tracked(iter([1, 2]))] == [1, 2]
    asyncio.run(scenario())


def test_iterator_stopped_before_dispatch_reports_budget_instead_of_hanging():
    async def scenario():
        budget = RequestBudget()
        budget.stop('task_timeout')
        work = RequestWork(RequestLimiter())
        called = []
        def source():
            called.append(True)
            yield 1
        with request_budget_scope(budget), request_work_scope(work):
            async with asyncio.timeout(.2):
                with pytest.raises(BudgetExceeded, match='task_timeout'):
                    await anext(iterate_sync_tracked(source()))
        assert called == []
        await wait_until(lambda: work.snapshot()['workers'] == 0)
    asyncio.run(scenario())


def test_iterator_constructor_failure_reaches_consumer():
    class BrokenIterable:
        def __iter__(self):
            raise ValueError('cannot iterate')
    async def scenario():
        async with asyncio.timeout(.2):
            with pytest.raises(ValueError, match='cannot iterate'):
                await anext(iterate_sync_tracked(BrokenIterable()))
    asyncio.run(scenario())
