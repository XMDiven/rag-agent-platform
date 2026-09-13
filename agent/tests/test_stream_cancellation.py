import asyncio
from threading import Event

from langchain_core.messages import AIMessageChunk

from agent_app.orchestration.async_streaming import stream_agent_loop_async, stream_until_disconnect
from agent_app.orchestration.cancellation import cancel_event
from agent_app.orchestration.executor import run_decomposed_retrieval


class Request:
    disconnected = False

    async def is_disconnected(self):
        return self.disconnected


def test_disconnect_closes_model_waiting_for_first_token():
    async def scenario():
        request = Request()
        started, closed = asyncio.Event(), asyncio.Event()

        class Model:
            def bind_tools(self, *args, **kwargs):
                return self

            async def astream(self, messages):
                try:
                    started.set()
                    await asyncio.Event().wait()
                    yield AIMessageChunk(content="unreachable")
                finally:
                    closed.set()

        async def consume():
            return [event async for event in stream_until_disconnect(request, stream_agent_loop_async("q", Model()))]

        task = asyncio.create_task(consume())
        await started.wait()
        request.disconnected = True
        assert await asyncio.wait_for(task, 1) == []
        assert closed.is_set()

    asyncio.run(scenario())


def test_normal_async_answer_keeps_event_order():
    async def scenario():
        class Model:
            def bind_tools(self, *args, **kwargs):
                return self

            async def astream(self, messages):
                yield AIMessageChunk(content="hello")
                yield AIMessageChunk(content=" world")

        events = [event async for event in stream_until_disconnect(Request(), stream_agent_loop_async("q", Model()))]
        assert [event.type for event in events] == ["answer_delta", "answer_delta", "sources", "done"]

    asyncio.run(scenario())


def test_cancelled_decomposition_does_not_start_next_subquestion(monkeypatch):
    stop = Event()
    calls = []
    monkeypatch.setattr("agent_app.orchestration.executor.run_question_decompose_tool", lambda q: {"sub_questions": ["a", "b"]})

    def retrieve(q):
        calls.append(q)
        stop.set()
        return {"answer": "a", "sources": []}

    monkeypatch.setattr("agent_app.orchestration.executor.run_retrieval_tool", retrieve)
    token = cancel_event.set(stop)
    try:
        import pytest
        with pytest.raises(asyncio.CancelledError):
            run_decomposed_retrieval("q")
    finally:
        cancel_event.reset(token)
    assert calls == ["a"]


def test_disconnect_during_tool_sets_cooperative_stop_without_next_round():
    async def scenario():
        request = Request()
        started = asyncio.Event()
        release = Event()
        observed = []
        rounds = []
        loop = asyncio.get_running_loop()

        class Model:
            def bind_tools(self, *args, **kwargs):
                return self

            async def astream(self, messages):
                rounds.append(1)
                yield AIMessageChunk(content="", tool_call_chunks=[{
                    "name": "retrieval_tool", "args": '{"question":"q"}', "id": "c1", "index": 0,
                }])

        def tool(name, args):
            observed.append(cancel_event.get())
            loop.call_soon_threadsafe(started.set)
            release.wait(2)
            return None

        async def consume():
            return [e async for e in stream_until_disconnect(request, stream_agent_loop_async("q", Model(), tool))]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), 1)
            request.disconnected = True
            assert await asyncio.wait_for(task, 1) == []
            assert observed[0].is_set()
            assert len(rounds) == 1
        finally:
            release.set()

    asyncio.run(scenario())
