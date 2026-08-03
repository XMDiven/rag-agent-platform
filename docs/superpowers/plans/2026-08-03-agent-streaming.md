# Agent Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backward-compatible, genuinely streaming Agent path that emits versioned NDJSON events through FastAPI and the Next.js BFF, then renders tool steps and answer chunks incrementally in React.

**Architecture:** Keep the existing JSON endpoints unchanged and add `/agent/run/stream` plus `/api/agent/stream`. A dedicated streaming loop consumes LangChain `AIMessageChunk` objects, emits typed events, and a browser-side parser converts arbitrary byte chunks into validated events and reducer state.

**Tech Stack:** Python 3.11+, FastAPI `StreamingResponse`, LangChain `AIMessageChunk`, Pydantic v2, pytest, Next.js App Router, TypeScript Web Streams, Node test runner, uv, npm

---

## File map

- `agent/src/agent_app/schemas/stream.py`: typed protocol events and NDJSON encoder.
- `agent/src/agent_app/orchestration/streaming.py`: true model streaming, tool-call chunk aggregation, event order, max-step finalization.
- `agent/src/agent_app/streaming_service.py`: query analysis, pre-stream fallback, safe error boundary, event-to-NDJSON generator.
- `agent/src/agent_app/app/routers/run.py`: new FastAPI `StreamingResponse` endpoint; existing endpoint remains unchanged.
- `agent/tests/test_stream_events.py`: schema and serialization contract.
- `agent/tests/test_streaming_loop.py`: offline chunk/tool orchestration tests.
- `agent/tests/test_streaming_service.py`: fallback and safe failure isolation.
- `agent/tests/api/test_run_api.py`: HTTP media type and event-order tests.
- `frontend/app/api/agent/stream/route.ts`: server-only upstream re-streaming BFF.
- `frontend/app/agent-stream.ts`: event validation, byte-stream parser, state reducer.
- `frontend/app/page.tsx`: incremental fetch/reader integration and partial-result rendering.
- `frontend/tests/agent-stream-route.test.ts`: BFF validation and non-buffering behavior.
- `frontend/tests/agent-stream.test.ts`: UTF-8 parsing, early EOF, protocol validation, reducer behavior.
- `compose.yaml`: server-only streaming upstream URL.
- `README.md`, `agent/README.md`: usage, limitations, and evidence.
- `/Users/mdiven/Code/Projects/rag-agent-platform/resume_alignment.md`: ignored living roadmap, updated only after a successful local merge.

### Task 1: Define the versioned NDJSON protocol

**Files:**
- Create: `agent/tests/test_stream_events.py`
- Create: `agent/src/agent_app/schemas/stream.py`
- Modify: `agent/src/agent_app/schemas/__init__.py`

- [ ] **Step 1: Write failing event serialization tests**

Create tests that instantiate every public event, assert exact JSON shape, and verify each encoded event is one newline-terminated JSON object:

```python
import json

from agent_app.schemas.stream import (
    AnswerDeltaData,
    AnswerDeltaEvent,
    DoneData,
    DoneEvent,
    ErrorData,
    ErrorEvent,
    SourcesData,
    SourcesEvent,
    StepData,
    StepEvent,
    encode_event,
)


def test_encode_step_event_as_one_ndjson_line() -> None:
    event = StepEvent(
        data=StepData(
            round=1,
            status="tool_executed",
            tool_name="retrieval_tool",
            tool_args={"question": "什么是 RAG？"},
            tool_status="success",
        )
    )

    encoded = encode_event(event)

    assert encoded.endswith("\n")
    assert encoded.count("\n") == 1
    assert json.loads(encoded) == {
        "version": 1,
        "type": "step",
        "data": {
            "round": 1,
            "status": "tool_executed",
            "tool_name": "retrieval_tool",
            "tool_args": {"question": "什么是 RAG？"},
            "tool_status": "success",
        },
    }


def test_all_terminal_events_have_stable_shapes() -> None:
    events = [
        AnswerDeltaEvent(data=AnswerDeltaData(text="RAG")),
        SourcesEvent(data=SourcesData(sources=[{"source": "rag.md"}])),
        ErrorEvent(
            data=ErrorData(
                code="agent_stream_failed",
                message="Agent 流式执行失败，请重试",
            )
        ),
        DoneEvent(
            data=DoneData(
                termination_reason="final_answer",
                selected_tool="retrieval_tool",
                tool_status="success",
            )
        ),
    ]

    assert [json.loads(encode_event(event))["type"] for event in events] == [
        "answer_delta",
        "sources",
        "error",
        "done",
    ]
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
uv run pytest agent/tests/test_stream_events.py -q
```

Expected: collection fails with `ModuleNotFoundError: agent_app.schemas.stream`.

- [ ] **Step 3: Implement the minimal typed event module**

Create Pydantic models with these exact public names and fields:

```python
from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field


class StepData(BaseModel):
    round: int = Field(ge=1)
    status: Literal["tool_executed", "tool_failed"]
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_status: Literal["success", "failed"]


class AnswerDeltaData(BaseModel):
    text: str = Field(min_length=1)


class SourcesData(BaseModel):
    sources: list[dict[str, Any]] = Field(default_factory=list)


class ErrorData(BaseModel):
    code: Literal[
        "agent_stream_failed",
        "mixed_model_output",
        "empty_model_output",
    ]
    message: str


class DoneData(BaseModel):
    termination_reason: Literal[
        "final_answer",
        "max_steps",
        "failed",
        "single_step",
    ]
    selected_tool: str
    tool_status: Literal["success", "failed"]


class StepEvent(BaseModel):
    version: Literal[1] = 1
    type: Literal["step"] = "step"
    data: StepData


class AnswerDeltaEvent(BaseModel):
    version: Literal[1] = 1
    type: Literal["answer_delta"] = "answer_delta"
    data: AnswerDeltaData


class SourcesEvent(BaseModel):
    version: Literal[1] = 1
    type: Literal["sources"] = "sources"
    data: SourcesData


class ErrorEvent(BaseModel):
    version: Literal[1] = 1
    type: Literal["error"] = "error"
    data: ErrorData


class DoneEvent(BaseModel):
    version: Literal[1] = 1
    type: Literal["done"] = "done"
    data: DoneData


AgentStreamEvent: TypeAlias = (
    StepEvent | AnswerDeltaEvent | SourcesEvent | ErrorEvent | DoneEvent
)


def encode_event(event: AgentStreamEvent) -> str:
    return event.model_dump_json() + "\n"
```

Export only the event types and `encode_event` from `schemas/__init__.py`; do not add a new dependency.

- [ ] **Step 4: Verify GREEN and validation edges**

Add assertions that empty `AnswerDeltaData.text` and `StepData.round=0` raise Pydantic validation errors, then run:

```bash
uv run pytest agent/tests/test_stream_events.py -q
```

Expected: all protocol tests pass.

- [ ] **Step 5: Commit the protocol**

```bash
git add agent/src/agent_app/schemas/stream.py agent/src/agent_app/schemas/__init__.py agent/tests/test_stream_events.py
git commit -m "feat(agent): define streaming event protocol"
```

### Task 2: Stream model text and assemble tool-call chunks

**Files:**
- Modify: `agent/tests/conftest.py`
- Create: `agent/tests/test_streaming_loop.py`
- Create: `agent/src/agent_app/orchestration/streaming.py`
- Modify: `agent/src/agent_app/orchestration/__init__.py`

- [ ] **Step 1: Add a fake streaming model and write RED text-chunk test**

Extend `agent/tests/conftest.py` with a fake that records `tool_choice` and returns one scripted list per `stream()` call:

```python
from langchain_core.messages import AIMessageChunk


class FakeStreamingChatModel:
    def __init__(
        self,
        responses: Sequence[Sequence[AIMessageChunk]],
    ) -> None:
        self._responses = [list(response) for response in responses]
        self.tool_choices: list[str] = []

    def bind_tools(self, tools: Any, tool_choice: str = "auto"):
        self.tool_choices.append(tool_choice)
        return self

    def stream(self, messages: Any):
        yield from self._responses.pop(0)


@pytest.fixture
def make_streaming_llm():
    def _make(
        responses: Sequence[Sequence[AIMessageChunk]],
    ) -> FakeStreamingChatModel:
        return FakeStreamingChatModel(responses)

    return _make
```

Write the wished-for behavior:

```python
from langchain_core.messages import AIMessageChunk

from agent_app.orchestration.streaming import stream_agent_loop


def test_stream_agent_loop_emits_real_answer_chunks(
    make_streaming_llm,
) -> None:
    llm = make_streaming_llm(
        [[AIMessageChunk(content="RAG "), AIMessageChunk(content="answer")]]
    )

    events = list(
        stream_agent_loop(
            question="What is RAG?",
            llm=llm,
            execute_tool=lambda name, args: None,
            max_steps=4,
        )
    )

    assert [event.type for event in events] == [
        "answer_delta",
        "answer_delta",
        "sources",
        "done",
    ]
    assert "".join(
        event.data.text
        for event in events
        if event.type == "answer_delta"
    ) == "RAG answer"
    assert events[-1].data.termination_reason == "final_answer"
```

- [ ] **Step 2: Verify RED**

```bash
uv run pytest agent/tests/test_streaming_loop.py::test_stream_agent_loop_emits_real_answer_chunks -q
```

Expected: import fails because `orchestration.streaming` does not exist.

- [ ] **Step 3: Implement minimal answer-mode streaming**

Create `stream_agent_loop()` with these dependencies and helpers:

```python
import json
from collections.abc import Callable, Iterator
from typing import Any, Literal

from langchain_core.messages import (
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_chunk_to_message,
)

from agent_app.orchestration.executor import ToolResult
from agent_app.orchestration.loop import (
    _FALLBACK_ANSWER,
    _SKIPPED_TOOL_PAYLOAD,
    build_failed_tool_result,
    build_loop_tools,
    collect_tool_sources,
    compact_tool_payload,
)
from agent_app.prompts import AGENT_LOOP_SYSTEM_PROMPT
from agent_app.schemas.stream import (
    AgentStreamEvent,
    AnswerDeltaData,
    AnswerDeltaEvent,
    DoneData,
    DoneEvent,
    SourcesData,
    SourcesEvent,
    StepData,
    StepEvent,
)


OutputMode = Literal["tool", "answer"]


class MixedModelOutputError(RuntimeError):
    pass


class EmptyModelOutputError(RuntimeError):
    pass


def chunk_text(chunk: AIMessageChunk) -> str:
    return chunk.content if isinstance(chunk.content, str) else ""


def stream_agent_loop(
    question: str,
    llm: Any,
    execute_tool: Callable[[str, dict[str, Any]], ToolResult],
    max_steps: int = 4,
) -> Iterator[AgentStreamEvent]:
    messages: list[BaseMessage] = [
        SystemMessage(content=AGENT_LOOP_SYSTEM_PROMPT),
        HumanMessage(content=question),
    ]
    tool_results: list[ToolResult] = []
    selected_tool = "fallback_tool"
    tool_calling_llm = llm.bind_tools(build_loop_tools(), tool_choice="auto")

    for round_index in range(1, max_steps + 1):
        combined: AIMessageChunk | None = None
        mode: OutputMode | None = None

        for chunk in tool_calling_llm.stream(messages):
            combined = chunk if combined is None else combined + chunk
            has_tool_chunk = bool(chunk.tool_call_chunks)
            text = chunk_text(chunk)

            if has_tool_chunk:
                if mode == "answer":
                    raise MixedModelOutputError
                mode = "tool"

            if text:
                if mode == "tool":
                    raise MixedModelOutputError
                mode = "answer"
                yield AnswerDeltaEvent(data=AnswerDeltaData(text=text))

        if combined is None or mode is None:
            raise EmptyModelOutputError

        ai_message = message_chunk_to_message(combined)
        messages.append(ai_message)

        if mode == "answer":
            yield SourcesEvent(
                data=SourcesData(sources=collect_tool_sources(tool_results))
            )
            yield DoneEvent(
                data=DoneData(
                    termination_reason="final_answer",
                    selected_tool=selected_tool,
                    tool_status="success",
                )
            )
            return

        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls:
            raise EmptyModelOutputError

        tool_call = tool_calls[0]
        tool_name = str(tool_call["name"])
        tool_args = tool_call.get("args") or {}
        selected_tool = tool_name

        try:
            tool_result = execute_tool(tool_name, tool_args)
        except Exception as error:
            tool_result = build_failed_tool_result(tool_name, error)

        tool_results.append(tool_result)
        yield StepEvent(
            data=StepData(
                round=round_index,
                status=(
                    "tool_failed"
                    if tool_result.status == "failed"
                    else "tool_executed"
                ),
                tool_name=tool_name,
                tool_args=tool_args,
                tool_status=(
                    "failed" if tool_result.status == "failed" else "success"
                ),
            )
        )
        messages.append(
            ToolMessage(
                content=json.dumps(
                    compact_tool_payload(tool_result), ensure_ascii=False
                ),
                tool_call_id=str(tool_call["id"]),
            )
        )
        for skipped_call in tool_calls[1:]:
            messages.append(
                ToolMessage(
                    content=json.dumps(
                        _SKIPPED_TOOL_PAYLOAD, ensure_ascii=False
                    ),
                    tool_call_id=str(skipped_call["id"]),
                )
            )

    emitted_text = False
    final_llm = llm.bind_tools(build_loop_tools(), tool_choice="none")
    for chunk in final_llm.stream(messages):
        if chunk.tool_call_chunks:
            raise MixedModelOutputError
        text = chunk_text(chunk)
        if text:
            emitted_text = True
            yield AnswerDeltaEvent(data=AnswerDeltaData(text=text))

    if not emitted_text:
        yield AnswerDeltaEvent(data=AnswerDeltaData(text=_FALLBACK_ANSWER))

    yield SourcesEvent(
        data=SourcesData(sources=collect_tool_sources(tool_results))
    )
    yield DoneEvent(
        data=DoneData(
            termination_reason="max_steps" if emitted_text else "failed",
            selected_tool=selected_tool,
            tool_status="success" if emitted_text else "failed",
        )
    )
```

For each round, bind `tool_choice="auto"`, aggregate chunks with `combined = chunk if combined is None else combined + chunk`, and immediately yield a non-empty content chunk as `AnswerDeltaEvent`. When the stream ends in answer mode, yield `SourcesEvent([])` and a successful `DoneEvent`, then return. Do not call `invoke()` or split a completed string.

- [ ] **Step 4: Verify answer-mode GREEN**

```bash
uv run pytest agent/tests/test_streaming_loop.py::test_stream_agent_loop_emits_real_answer_chunks -q
```

Expected: pass and `llm.tool_choices == ["auto"]`.

- [ ] **Step 5: Write RED test for split tool-call chunks and event order**

Use two `AIMessageChunk` objects whose `tool_call_chunks` form one JSON argument, followed by an answer response:

```python
def tool_chunks() -> list[AIMessageChunk]:
    return [
        AIMessageChunk(
            content="",
            tool_call_chunks=[{
                "name": "retrieval_tool",
                "args": '{"ques',
                "id": "call_1",
                "index": 0,
                "type": "tool_call_chunk",
            }],
        ),
        AIMessageChunk(
            content="",
            tool_call_chunks=[{
                "name": None,
                "args": 'tion":"What is RAG?"}',
                "id": None,
                "index": 0,
                "type": "tool_call_chunk",
            }],
        ),
    ]


def test_stream_agent_loop_executes_assembled_tool_once(
    make_streaming_llm,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    llm = make_streaming_llm([
        tool_chunks(),
        [AIMessageChunk(content="Grounded answer")],
    ])

    def execute(name, args):
        calls.append((name, args))
        return ToolResult(
            tool_name=name,
            status="success",
            output={"answer": "ctx", "sources": [{"source": "rag.md"}]},
            attempts=[{"attempt": 1, "status": "success"}],
        )

    events = list(stream_agent_loop("What is RAG?", llm, execute))

    assert calls == [("retrieval_tool", {"question": "What is RAG?"})]
    assert [event.type for event in events] == [
        "step", "answer_delta", "sources", "done"
    ]
    assert events[2].data.sources == [{"source": "rag.md"}]
    assert events[3].data.selected_tool == "retrieval_tool"
```

- [ ] **Step 6: Verify tool test RED, then implement tool mode**

Run the single test and confirm it fails because no tool is executed. Implement:

- `mode="tool"` when a chunk contains `tool_call_chunks`;
- reject content after tool mode or tool chunks after answer mode with a dedicated `MixedModelOutputError`;
- convert the accumulated chunk with `message_chunk_to_message(combined)` and append it to `messages`;
- execute only `combined.tool_calls[0]`;
- convert thrown tool exceptions with existing `build_failed_tool_result()`;
- append one safe `StepEvent` without exception content;
- append compact `ToolMessage` payloads for the executed and skipped calls exactly as the current loop does.

Run:

```bash
uv run pytest agent/tests/test_streaming_loop.py -q
```

Expected: answer and tool-mode tests pass.

- [ ] **Step 7: Add RED/GREEN tests for failure recovery, mixed output, and max steps**

Add three focused tests:

1. Tool raises `RuntimeError("api_key=secret")`, a `tool_failed` step is emitted without the secret, and a later answer completes normally.
2. One model round emits `AIMessageChunk(content="partial")` and then a tool chunk; iteration raises `MixedModelOutputError`.
3. `max_steps=1` with one tool call causes a second `tool_choice="none"` stream; its chunks become `answer_delta`, followed by `sources` and `done(termination_reason="max_steps")`.

Use the exact safety assertion:

```python
serialized = "\n".join(event.model_dump_json() for event in events)
assert "api_key" not in serialized
assert "secret" not in serialized
```

Implement max-step streaming with `llm.bind_tools(build_loop_tools(), tool_choice="none").stream(messages)`. If it yields no text, emit the existing fallback text once and `DoneEvent(termination_reason="failed", tool_status="failed")`.

Run:

```bash
uv run pytest agent/tests/test_streaming_loop.py -q
```

Expected: all streaming-loop tests pass.

- [ ] **Step 8: Commit the streaming loop**

```bash
git add agent/src/agent_app/orchestration/streaming.py agent/src/agent_app/orchestration/__init__.py agent/tests/conftest.py agent/tests/test_streaming_loop.py
git commit -m "feat(agent): stream agent loop events"
```

### Task 3: Add the service error boundary and FastAPI endpoint

**Files:**
- Create: `agent/tests/test_streaming_service.py`
- Create: `agent/src/agent_app/streaming_service.py`
- Modify: `agent/src/agent_app/app/routers/run.py`
- Modify: `agent/tests/api/test_run_api.py`

- [ ] **Step 1: Write RED tests for single-step fallback and safe stream errors**

Define a dependency-injected API:

```python
events = list(
    stream_agent_events(
        "",
        analyze_fn=fake_analysis,
        run_once_fn=fake_run_once,
        stream_loop_fn=fake_stream_loop,
        get_llm_fn=lambda: object(),
        execute_tool_fn=fake_execute,
    )
)
```

Assert the empty/single-step result produces:

```text
answer_delta → sources → done(single_step)
```

Then make `fake_stream_loop` yield one `StepEvent` and raise `RuntimeError("api_key=secret")`. Assert output is:

```text
step → error(agent_stream_failed) → done(failed)
```

and serialized events contain neither `api_key` nor `secret`.

- [ ] **Step 2: Verify service tests RED**

```bash
uv run pytest agent/tests/test_streaming_service.py -q
```

Expected: import fails because `streaming_service` does not exist.

- [ ] **Step 3: Implement `stream_agent_events()` and `stream_agent_ndjson()`**

Use existing `analyze_query`, `should_skip_loop`, `run_agent_once`, `get_client`, `run_tool`, and `AGENT_MAX_STEPS`. Add helpers that extract answer/sources from an `AgentRunResult` without importing router functions.

Behavior must be exact:

- single-step result: non-empty `answer_delta`, one `sources`, one `done(single_step)`;
- loop exception before the first event: call `run_agent_once()` and serialize it;
- loop exception after at least one event: emit stable `ErrorEvent` and failed `DoneEvent`;
- `MixedModelOutputError`: emit `ErrorEvent(code="mixed_model_output")` and failed `DoneEvent`;
- never persist or emit `str(error)`;
- `stream_agent_ndjson(question)` maps every event through `encode_event()`.

Use this public signature:

```python
import logging
from collections.abc import Iterator
from typing import Any

from agent_app.schemas.stream import (
    AgentStreamEvent,
    AnswerDeltaData,
    AnswerDeltaEvent,
    DoneData,
    DoneEvent,
    ErrorData,
    ErrorEvent,
    SourcesData,
    SourcesEvent,
    encode_event,
)


logger = logging.getLogger(__name__)


def result_output(result: Any) -> dict[str, Any]:
    output = result.tool_result.output
    return output if isinstance(output, dict) else {}


def failed_done_event() -> DoneEvent:
    return DoneEvent(
        data=DoneData(
            termination_reason="failed",
            selected_tool="fallback_tool",
            tool_status="failed",
        )
    )


def events_from_single_result(result: Any) -> Iterator[AgentStreamEvent]:
    output = result_output(result)
    answer = output.get("answer")
    sources = output.get("sources")

    if not isinstance(answer, str) or not answer:
        yield ErrorEvent(
            data=ErrorData(
                code="empty_model_output",
                message="Agent 未生成有效回答，请重试",
            )
        )
        yield failed_done_event()
        return

    yield AnswerDeltaEvent(data=AnswerDeltaData(text=answer))
    yield SourcesEvent(
        data=SourcesData(
            sources=(
                [item for item in sources if isinstance(item, dict)]
                if isinstance(sources, list)
                else []
            )
        )
    )
    yield DoneEvent(
        data=DoneData(
            termination_reason="single_step",
            selected_tool=result.plan.tool.name,
            tool_status=(
                "failed"
                if result.tool_result.status == "failed"
                else "success"
            ),
        )
    )


def stream_agent_events(
    question: str,
    analyze_fn=analyze_query,
    run_once_fn=run_agent_once,
    stream_loop_fn=stream_agent_loop,
    get_llm_fn=get_client,
    execute_tool_fn=run_tool,
) -> Iterator[AgentStreamEvent]:
    analysis = analyze_fn(question)

    if should_skip_loop(analysis):
        yield from events_from_single_result(run_once_fn(question, analysis))
        return

    emitted = False
    try:
        for event in stream_loop_fn(
            question=question,
            llm=get_llm_fn(),
            execute_tool=execute_tool_fn,
            max_steps=AGENT_MAX_STEPS,
        ):
            emitted = True
            yield event
    except MixedModelOutputError:
        yield ErrorEvent(
            data=ErrorData(
                code="mixed_model_output",
                message="模型返回了不兼容的混合流，请重试",
            )
        )
        yield failed_done_event()
    except Exception as error:
        logger.warning("agent.stream failed error_type=%s", type(error).__name__)
        if not emitted:
            yield from events_from_single_result(
                run_once_fn(question, analysis)
            )
            return
        yield ErrorEvent(
            data=ErrorData(
                code="agent_stream_failed",
                message="Agent 流式执行失败，请重试",
            )
        )
        yield failed_done_event()


def stream_agent_ndjson(question: str) -> Iterator[str]:
    for event in stream_agent_events(question):
        yield encode_event(event)
```

- [ ] **Step 4: Verify service GREEN**

```bash
uv run pytest agent/tests/test_streaming_service.py -q
```

Expected: fallback, error isolation, and event-order tests pass.

- [ ] **Step 5: Write RED HTTP tests**

Monkeypatch `agent_app.app.routers.run.stream_agent_ndjson` to return deterministic lines. Assert:

```python
response = client.post("/agent/run/stream", json={"question": "RAG?"})
assert response.status_code == 200
assert response.headers["content-type"].startswith("application/x-ndjson")
assert response.headers["cache-control"] == "no-cache"
assert response.headers["x-content-type-options"] == "nosniff"
assert [json.loads(line)["type"] for line in response.text.splitlines()] == [
    "answer_delta", "sources", "done"
]
```

Keep the existing `/agent/run` assertions unchanged.

- [ ] **Step 6: Implement the FastAPI route and verify GREEN**

Add to `run.py`:

```python
from fastapi.responses import StreamingResponse

from agent_app.streaming_service import stream_agent_ndjson


@router.post("/agent/run/stream")
def stream_agent_endpoint(request: AgentRunRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_agent_ndjson(request.question),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
```

Run:

```bash
uv run pytest agent/tests/test_streaming_service.py agent/tests/api/test_run_api.py -q
```

Expected: all focused service/API tests pass.

- [ ] **Step 7: Commit the service and endpoint**

```bash
git add agent/src/agent_app/streaming_service.py agent/src/agent_app/app/routers/run.py agent/tests/test_streaming_service.py agent/tests/api/test_run_api.py
git commit -m "feat(agent): expose NDJSON streaming endpoint"
```

### Task 4: Re-stream through a server-only Next.js BFF

**Files:**
- Create: `frontend/tests/agent-stream-route.test.ts`
- Create: `frontend/app/api/agent/stream/route.ts`
- Modify: `compose.yaml`

- [ ] **Step 1: Write RED BFF validation and streaming tests**

Test valid forwarding to `AGENT_STREAM_API_URL`, blank/malformed input rejection, and stable 502. For non-buffering, create a controlled `ReadableStream`:

```typescript
test("re-streams the first upstream chunk before upstream closes", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const encoder = new TextEncoder();
  const upstreamBody = new ReadableStream<Uint8Array>({
    start(value) { controller = value; },
  });

  globalThis.fetch = async () => new Response(upstreamBody, {
    headers: {"Content-Type": "application/x-ndjson"},
  });

  const response = await POST(validRequest());
  const reader = response.body!.getReader();
  controller.enqueue(encoder.encode('{"version":1,"type":"answer_delta","data":{"text":"A"}}\n'));

  const first = await reader.read();

  assert.equal(new TextDecoder().decode(first.value), '{"version":1,"type":"answer_delta","data":{"text":"A"}}\n');
  controller.close();
});
```

- [ ] **Step 2: Verify BFF test RED**

```bash
cd frontend
node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test --experimental-strip-types tests/agent-stream-route.test.ts
```

Expected: module import fails because the stream Route Handler does not exist.

- [ ] **Step 3: Implement the stream Route Handler**

Mirror the existing input validator, but forward to:

```typescript
const DEFAULT_AGENT_STREAM_API_URL =
  "http://localhost:8002/agent/run/stream";
```

Return the upstream body without reading it:

```typescript
return new Response(upstream.body, {
  status: upstream.status,
  headers: {
    "Content-Type":
      upstream.headers.get("content-type") ?? "application/x-ndjson",
    "Cache-Control": "no-cache",
    "X-Content-Type-Options": "nosniff",
  },
});
```

Use only `process.env.AGENT_STREAM_API_URL`; never accept a URL from the request body.

- [ ] **Step 4: Configure Compose and verify GREEN**

Add only this frontend server environment value:

```yaml
AGENT_STREAM_API_URL: http://agent-api:8002/agent/run/stream
```

Run:

```bash
cd frontend
npm test
cd ..
docker compose --env-file .env.example config --quiet
```

Expected: existing and new BFF tests pass; Compose config exits 0.

- [ ] **Step 5: Commit the BFF**

```bash
git add frontend/app/api/agent/stream/route.ts frontend/tests/agent-stream-route.test.ts compose.yaml
git commit -m "feat(frontend): proxy agent stream through BFF"
```

### Task 5: Parse arbitrary NDJSON byte chunks and reduce event state

**Files:**
- Create: `frontend/tests/agent-stream.test.ts`
- Create: `frontend/app/agent-stream.ts`

- [ ] **Step 1: Write RED protocol parser tests**

Test an answer event, unknown version, unknown type, and invalid data. The public API is:

```typescript
const event = parseAgentEvent(
  '{"version":1,"type":"answer_delta","data":{"text":"你好"}}',
);
assert.deepEqual(event, {
  version: 1,
  type: "answer_delta",
  data: {text: "你好"},
});
assert.throws(() => parseAgentEvent('{"version":2,"type":"done","data":{}}'));
```

Define a TypeScript discriminated union matching the Python schema exactly; validate fields with explicit type guards rather than type assertions alone.

- [ ] **Step 2: Verify parser test RED**

```bash
cd frontend
node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test --experimental-strip-types tests/agent-stream.test.ts
```

Expected: module import fails because `app/agent-stream.ts` does not exist.

- [ ] **Step 3: Implement event types and strict parser**

Export:

```typescript
export type AgentStreamEvent =
  | {version: 1; type: "step"; data: AgentStep}
  | {version: 1; type: "answer_delta"; data: {text: string}}
  | {version: 1; type: "sources"; data: {sources: Array<Record<string, unknown>>}}
  | {version: 1; type: "error"; data: {code: string; message: string}}
  | {version: 1; type: "done"; data: DoneData};

export class AgentStreamProtocolError extends Error {
  constructor(public readonly code: string) {
    super(code);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function parseAgentEvent(line: string): AgentStreamEvent {
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    throw new AgentStreamProtocolError("invalid_json");
  }

  if (!isRecord(value) || value.version !== 1 || !isRecord(value.data)) {
    throw new AgentStreamProtocolError(
      isRecord(value) && value.version !== 1
        ? "unsupported_version"
        : "invalid_event",
    );
  }

  const data = value.data;
  switch (value.type) {
    case "answer_delta":
      if (typeof data.text !== "string" || !data.text) {
        throw new AgentStreamProtocolError("invalid_event");
      }
      break;
    case "step":
      if (
        typeof data.round !== "number" ||
        data.round < 1 ||
        typeof data.status !== "string" ||
        typeof data.tool_name !== "string" ||
        !isRecord(data.tool_args) ||
        typeof data.tool_status !== "string"
      ) {
        throw new AgentStreamProtocolError("invalid_event");
      }
      break;
    case "sources":
      if (!Array.isArray(data.sources)) {
        throw new AgentStreamProtocolError("invalid_event");
      }
      break;
    case "error":
      if (
        typeof data.code !== "string" ||
        typeof data.message !== "string"
      ) {
        throw new AgentStreamProtocolError("invalid_event");
      }
      break;
    case "done":
      if (
        typeof data.termination_reason !== "string" ||
        typeof data.selected_tool !== "string" ||
        typeof data.tool_status !== "string"
      ) {
        throw new AgentStreamProtocolError("invalid_event");
      }
      break;
    default:
      throw new AgentStreamProtocolError("invalid_event");
  }

  return value as AgentStreamEvent;
}
```

The parser must reject missing `version/type/data`, `version !== 1`, unsupported type, empty answer text, malformed step fields, non-array sources, and incomplete done metadata using stable codes such as `invalid_json`, `unsupported_version`, and `invalid_event`.

- [ ] **Step 4: Write RED stream-boundary tests**

Create byte streams that cover:

- two lines in one chunk;
- one line split over two chunks;
- the three UTF-8 bytes of `你` split between chunks;
- EOF without `done`;
- final valid line without a trailing newline.

Call:

```typescript
const received: AgentStreamEvent[] = [];
await readAgentStream(stream, (event) => received.push(event));
```

Assert early EOF rejects with `AgentStreamProtocolError.code === "stream_ended_early"`.

- [ ] **Step 5: Implement `readAgentStream()` and verify GREEN**

Implement with `stream.getReader()`, one `TextDecoder`, `decode(value, {stream: true})`, and a string buffer split on `\n`. Track `sawDone`; reject any event after done and reject EOF without done. Release the reader lock in `finally`.

Run:

```bash
cd frontend
npm test
```

Expected: all route, parser, UTF-8, and early-EOF tests pass.

- [ ] **Step 6: Write RED reducer integration test**

Define initial state and apply a full event sequence:

```typescript
let state = createInitialAgentStreamState();
for (const event of events) {
  state = reduceAgentStreamState(state, event);
}

assert.equal(state.answer, "RAG answer");
assert.equal(state.steps.length, 1);
assert.deepEqual(state.sources, [{source: "rag.md"}]);
assert.equal(state.terminationReason, "final_answer");
assert.equal(state.completed, true);
```

The state type must include `answer`, `steps`, `sources`, `terminationReason`, `selectedTool`, `toolStatus`, `error`, and `completed`.

- [ ] **Step 7: Implement reducer and commit parser/state**

Reducer behavior:

- append `answer_delta.text`;
- append each `step` once;
- replace sources;
- set safe error message on `error` without discarding partial data;
- set done metadata and `completed=true` on `done`.

Run `npm test`, then commit:

```bash
git add frontend/app/agent-stream.ts frontend/tests/agent-stream.test.ts
git commit -m "feat(frontend): parse agent NDJSON stream"
```

### Task 6: Render streaming progress in React

**Files:**
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/tests/agent-stream.test.ts`

- [ ] **Step 1: Add a RED fake-stream integration test**

Feed a controlled stream through `readAgentStream()` and update state with `reduceAgentStreamState()`. Hold the stream open after the first `step`, then assert the state already contains that step and is not completed. Enqueue two answer deltas and done, close it, then assert the final state.

This proves the same parser/reducer used by the page exposes partial state before EOF without adding a React testing dependency.

- [ ] **Step 2: Verify integration test RED**

Run the single test and confirm it fails until the controlled-stream helper and reducer sequencing support partial observation.

- [ ] **Step 3: Replace page-level full JSON state with stream state**

In `page.tsx`:

- import `createInitialAgentStreamState`, `readAgentStream`, and `reduceAgentStreamState`;
- replace `AgentRunResponse | null` with `AgentStreamState` plus a `hasStarted` flag;
- POST to `/api/agent/stream`;
- reject non-2xx before reading the body;
- call `readAgentStream(response.body, event => setState(current => reduceAgentStreamState(current, event)))`;
- render answer and steps whenever `hasStarted`, including while `loading` is true;
- keep partial answer/steps when an `error` event or early EOF occurs;
- show `连接意外中断，请重试` for `stream_ended_early`;
- create one `AbortController` per request and abort it in a `useEffect` cleanup;
- do not add a stop button or change unrelated styles.

The existing source cards and `buildAgentStepViewModel()` remain in use. Done metadata replaces fields formerly read from `AgentRunResponse`.

- [ ] **Step 4: Verify page behavior and static checks**

```bash
cd frontend
npm test
npm run lint
npx tsc --noEmit
npm run build
```

Expected: tests pass, no lint/type errors, and Next build lists both `/api/agent` and `/api/agent/stream` dynamic routes.

- [ ] **Step 5: Commit the UI integration**

```bash
git add frontend/app/page.tsx frontend/tests/agent-stream.test.ts
git commit -m "feat(frontend): render streaming agent progress"
```

### Task 7: Document, verify, and run a real streaming smoke test

**Files:**
- Modify: `README.md`
- Modify: `agent/README.md`

- [ ] **Step 1: Document exact commands and boundaries**

Add a curl example that disables response buffering:

```bash
curl -N \
  -H 'Content-Type: application/json' \
  -d '{"question":"LangChain 和 LlamaIndex 有什么区别？"}' \
  http://localhost:8002/agent/run/stream
```

Document the five event types, that `/agent/run` remains compatible, `AGENT_STREAM_API_URL` is server-only, and synchronous upstream cancellation is a known first-version limitation.

- [ ] **Step 2: Run full deterministic verification**

```bash
uv run pytest -q
cd frontend
npm test
npm run lint
npx tsc --noEmit
npm run build
cd ..
docker compose --env-file .env.example config --quiet
git diff --check
```

Expected: all Python/Node tests pass, lint/type/build succeed, Compose validates, and no whitespace errors exist.

- [ ] **Step 3: Inspect security and compatibility**

Run:

```bash
if rg -n 'NEXT_PUBLIC_.*(KEY|TOKEN|SECRET)|api_key=|MOONSHOT_API_KEY|sk-[A-Za-z0-9]' frontend/.next; then exit 1; fi
if rg -n 'api_key=|MOONSHOT_API_KEY|sk-[A-Za-z0-9]' docs/demo/agent_streaming.md; then exit 1; fi
git diff b7c4dd5..HEAD -- agent/src/agent_app/app/routers/run.py frontend/app/api/agent/route.ts
```

Expected: no secret is embedded in `.next`; existing non-stream endpoints remain present and unchanged except imports required for the new route.

- [ ] **Step 4: Run the real stream and preserve evidence**

With `.env`, Qdrant, embedding model, RAG API, and Agent API available, run the curl command from Step 1 and save a short manually reviewed transcript under `docs/demo/agent_streaming.md`. Record timestamps or sequence numbers proving at least one `step` arrived before the final `answer_delta`/`done`. Do not save API keys, complete retrieved snippets, or raw exception messages.

- [ ] **Step 5: Commit documentation and evidence**

```bash
git add README.md agent/README.md docs/demo/agent_streaming.md
git commit -m "docs: record agent streaming demo"
```

- [ ] **Step 6: Request independent review and finish the branch**

Review the complete range from design base `b7c4dd5` through HEAD. Fix every Critical/Important issue with a new RED/GREEN test, rerun full verification, then use `superpowers:finishing-a-development-branch` to merge locally, push a PR, keep, or discard according to the user's choice. After a successful local merge, update ignored `/Users/mdiven/Code/Projects/rag-agent-platform/resume_alignment.md`: mark B-2b complete with `2026-08-03`, add the fresh Python/Node test counts, and record the verified event sequence. Never stage that ignored file.
