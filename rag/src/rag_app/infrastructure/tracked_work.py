"""Keep request ownership until blocking work actually exits its worker thread."""

import asyncio
from functools import partial
from threading import Event

import anyio

from rag_app.infrastructure.request_budget import check_request_budget
from rag_app.infrastructure.request_concurrency import get_request_work


_pending_tasks: set[asyncio.Task] = set()


def _retain(task: asyncio.Task, ticket=None) -> asyncio.Task:
    _pending_tasks.add(task)

    def finished(done):
        _pending_tasks.discard(done)
        if ticket is not None:
            # A task cancelled before dispatch must not leak its ticket. Once
            # started, the wrapper owns its ticket until its actual thread exits.
            ticket.withdraw_if_not_started()
        if not done.cancelled():
            done.exception()

    task.add_done_callback(finished)
    return task


def _dispatch(call) -> asyncio.Task:
    work = get_request_work()
    ticket = work.register_worker() if work is not None else None

    def guarded():
        if ticket is not None and not ticket.begin():
            return None
        try:
            if ticket is not None:
                check_request_budget()
            return call()
        finally:
            if ticket is not None:
                ticket.finish()

    return _retain(asyncio.create_task(anyio.to_thread.run_sync(guarded)), ticket)


async def run_sync_tracked(func, *args, **kwargs):
    call = partial(func, *args, **kwargs)
    if get_request_work() is None:
        return await anyio.to_thread.run_sync(call)
    # Cancellation of the HTTP/app waiter cannot cancel actual dispatch.
    return await asyncio.shield(_dispatch(call))


async def iterate_sync_tracked(iterable):
    """Stream from one producer thread with at most one queued item.

    Closing the receiving side wakes a producer blocked in send. A producer
    blocked in next keeps its ticket, then closes the iterator in its own thread.
    """
    sender, receiver = anyio.create_memory_object_stream(1)
    stopped = Event()

    def produce():
        iterator = iter(iterable)
        try:
            while not stopped.is_set():
                check_request_budget()
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                if stopped.is_set():
                    break
                try:
                    anyio.from_thread.run(sender.send, (True, item))
                except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                    break
        finally:
            close = getattr(iterator, 'close', None)
            if close is not None:
                close()

    # Register synchronously, before yielding control to the consumer's receive.
    worker = _dispatch(produce)

    async def supervise():
        try:
            await asyncio.shield(worker)
        except Exception as exc:
            if not stopped.is_set():
                try:
                    await sender.send((False, exc))
                except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                    pass
        finally:
            # This also closes the channel if a sticky budget prevents produce
            # from starting, or if constructing/closing the iterator fails.
            sender.close()

    producer = _retain(asyncio.create_task(supervise()))
    try:
        async with receiver:
            async for success, item in receiver:
                if not success:
                    raise item
                yield item
        await producer
    finally:
        stopped.set()
        # Synchronous close is cancellation-safe and unblocks a full channel.
        receiver.close()
