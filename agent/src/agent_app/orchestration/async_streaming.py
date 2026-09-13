"""Cancellable HTTP streaming; synchronous tools stop at cooperative boundaries."""

import asyncio
import json
from contextlib import aclosing
from threading import Event

import anyio
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, message_chunk_to_message

from agent_app.orchestration.cancellation import cancel_event
from agent_app.orchestration.executor import run_tool
from agent_app.orchestration.loop import (
    _FALLBACK_ANSWER, _SKIPPED_TOOL_PAYLOAD, build_failed_tool_result,
    build_loop_tools, collect_tool_sources, compact_tool_payload, count_tool_sources,
)
from agent_app.orchestration.streaming import EmptyModelOutputError, MixedModelOutputError, chunk_text
from agent_app.prompts import AGENT_LOOP_SYSTEM_PROMPT
from agent_app.schemas.stream import (
    AnswerDeltaData, AnswerDeltaEvent, DoneData, DoneEvent,
    SourcesData, SourcesEvent, StepData, StepEvent,
)


async def stream_agent_loop_async(question, llm, execute_tool=run_tool, max_steps=4):
    messages = [SystemMessage(content=AGENT_LOOP_SYSTEM_PROMPT), HumanMessage(content=question)]
    results = []
    next_source_index = 1
    selected_tool = "fallback_tool"
    stop = Event()
    try:
        for round_index in range(1, max_steps + 2):
            forced = round_index > max_steps
            model = llm.bind_tools(build_loop_tools(), tool_choice="none" if forced else "auto")
            combined = None
            mode = None
            async with aclosing(model.astream(messages)) as stream:
                async for chunk in stream:
                    combined = chunk if combined is None else combined + chunk
                    text = chunk_text(chunk)
                    if chunk.tool_call_chunks:
                        if forced or mode == "answer":
                            raise MixedModelOutputError
                        mode = "tool"
                    if text:
                        if mode == "tool":
                            raise MixedModelOutputError
                        mode = "answer"
                        yield AnswerDeltaEvent(data=AnswerDeltaData(text=text))

            if forced or mode == "answer":
                if forced and mode != "answer":
                    yield AnswerDeltaEvent(data=AnswerDeltaData(text=_FALLBACK_ANSWER))
                yield SourcesEvent(data=SourcesData(sources=collect_tool_sources(results)))
                yield DoneEvent(data=DoneData(
                    termination_reason=("failed" if mode != "answer" else "max_steps") if forced else "final_answer",
                    selected_tool=selected_tool,
                    tool_status="success" if mode == "answer" else "failed",
                ))
                return
            if combined is None or mode is None:
                raise EmptyModelOutputError
            message = message_chunk_to_message(combined)
            messages.append(message)
            calls = getattr(message, "tool_calls", None) or []
            if not calls:
                raise EmptyModelOutputError
            call = calls[0]
            selected_tool = str(call["name"])
            args = call.get("args") or {}
            try:
                # Cancelling the waiter cannot kill an in-flight sync request.
                # Context propagation lets decomposed tools skip remaining work.
                token = cancel_event.set(stop)
                try:
                    result = await anyio.to_thread.run_sync(
                        execute_tool, selected_tool, args, abandon_on_cancel=True,
                    )
                finally:
                    cancel_event.reset(token)
            except Exception as error:
                result = build_failed_tool_result(selected_tool, error)
            results.append(result)
            yield StepEvent(data=StepData(
                round=round_index,
                status="tool_failed" if result.status == "failed" else "tool_executed",
                tool_name=selected_tool, tool_args=args,
                tool_status="failed" if result.status == "failed" else "success",
            ))
            messages.append(ToolMessage(
                content=json.dumps(compact_tool_payload(result, next_source_index), ensure_ascii=False),
                tool_call_id=str(call["id"]),
            ))
            next_source_index += count_tool_sources(result)
            for skipped in calls[1:]:
                messages.append(ToolMessage(content=json.dumps(_SKIPPED_TOOL_PAYLOAD), tool_call_id=str(skipped["id"])))
    finally:
        stop.set()


async def stream_until_disconnect(request, events):
    """Monitor ASGI disconnect even while waiting for a model's first token."""
    async def watch():
        while True:
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.05)

    watcher = asyncio.create_task(watch())
    pending = None
    try:
        while True:
            pending = asyncio.create_task(anext(events))
            done, _ = await asyncio.wait((watcher, pending), return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                watcher.result()
                return
            try:
                yield pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
    finally:
        # Finish cancellation before closing a generator whose anext is active.
        watcher.cancel()
        if pending is not None:
            pending.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(watcher, *([pending] if pending is not None else []), return_exceptions=True)
            await events.aclose()
