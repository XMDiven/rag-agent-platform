import assert from "node:assert/strict";
import test from "node:test";

import {
  AgentStreamProtocolError,
  createInitialAgentStreamState,
  createStallWatchdog,
  parseAgentEvent,
  readAgentErrorMessage,
  readAgentStream,
  reduceAgentStreamState,
  type AgentStreamEvent,
} from "../app/agent-stream.ts";

const encoder = new TextEncoder();

function byteStream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

function line(event: unknown): string {
  return `${JSON.stringify(event)}\n`;
}

const answerEvent = {
  version: 1,
  type: "answer_delta",
  data: {text: "你好"},
} as const;

const doneEvent = {
  version: 1,
  type: "done",
  data: {
    termination_reason: "final_answer",
    selected_tool: "retrieval_tool",
    tool_status: "success",
  },
} as const;

test("parses a valid answer event", () => {
  assert.deepEqual(parseAgentEvent(JSON.stringify(answerEvent)), answerEvent);
});

test("rejects unsupported versions and malformed events", () => {
  assert.throws(
    () =>
      parseAgentEvent(
        '{"version":2,"type":"answer_delta","data":{"text":"x"}}',
      ),
    (error) =>
      error instanceof AgentStreamProtocolError &&
      error.code === "unsupported_version",
  );
  assert.throws(
    () =>
      parseAgentEvent(
        '{"version":1,"type":"unknown","data":{}}',
      ),
    (error) =>
      error instanceof AgentStreamProtocolError &&
      error.code === "invalid_event",
  );
  assert.throws(
    () =>
      parseAgentEvent(
        '{"version":1,"type":"answer_delta","data":{"text":""}}',
      ),
    (error) =>
      error instanceof AgentStreamProtocolError &&
      error.code === "invalid_event",
  );
  assert.throws(
    () =>
      parseAgentEvent(
        '{"version":1,"type":"done","data":{"termination_reason":"invented","selected_tool":"x","tool_status":"success"}}',
      ),
    (error) =>
      error instanceof AgentStreamProtocolError &&
      error.code === "invalid_event",
  );
  assert.throws(
    () =>
      parseAgentEvent(
        '{"version":1,"type":"step","data":{"round":1,"status":"invented","tool_name":"x","tool_args":{},"tool_status":"success"}}',
      ),
    (error) =>
      error instanceof AgentStreamProtocolError &&
      error.code === "invalid_event",
  );
});

test("reads multiple lines split across arbitrary chunks", async () => {
  const payload = line(answerEvent) + line(doneEvent);
  const bytes = encoder.encode(payload);
  const received: AgentStreamEvent[] = [];

  await readAgentStream(
    byteStream([bytes.slice(0, 17), bytes.slice(17)]),
    (event) => received.push(event),
  );

  assert.deepEqual(received, [answerEvent, doneEvent]);
});

test("decodes a Chinese character split between UTF-8 chunks", async () => {
  const bytes = encoder.encode(line(answerEvent) + line(doneEvent));
  const chineseStart = bytes.indexOf(0xe4);
  assert.notEqual(chineseStart, -1);
  const received: AgentStreamEvent[] = [];

  await readAgentStream(
    byteStream([
      bytes.slice(0, chineseStart + 1),
      bytes.slice(chineseStart + 1),
    ]),
    (event) => received.push(event),
  );

  assert.equal(received[0].type, "answer_delta");
  assert.equal(received[0].data.text, "你好");
});

test("accepts a final done line without a trailing newline", async () => {
  const payload = line(answerEvent) + JSON.stringify(doneEvent);
  const received: AgentStreamEvent[] = [];

  await readAgentStream(byteStream([encoder.encode(payload)]), (event) =>
    received.push(event),
  );

  assert.equal(received.at(-1)?.type, "done");
});

test("rejects EOF before done", async () => {
  await assert.rejects(
    readAgentStream(byteStream([encoder.encode(line(answerEvent))]), () => {}),
    (error) =>
      error instanceof AgentStreamProtocolError &&
      error.code === "stream_ended_early",
  );
});

test("reduces a complete event sequence without losing partial data", () => {
  const stepEvent = parseAgentEvent(
    line({
      version: 1,
      type: "step",
      data: {
        round: 1,
        status: "tool_executed",
        tool_name: "retrieval_tool",
        tool_args: {question: "RAG?"},
        tool_status: "success",
      },
    }).trim(),
  );
  const sourcesEvent = parseAgentEvent(
    line({
      version: 1,
      type: "sources",
      data: {sources: [{source: "rag.md"}]},
    }).trim(),
  );
  const events = [
    stepEvent,
    parseAgentEvent(
      '{"version":1,"type":"answer_delta","data":{"text":"RAG "}}',
    ),
    parseAgentEvent(
      '{"version":1,"type":"answer_delta","data":{"text":"answer"}}',
    ),
    sourcesEvent,
    parseAgentEvent(JSON.stringify(doneEvent)),
  ];
  let state = createInitialAgentStreamState();

  for (const event of events) {
    state = reduceAgentStreamState(state, event);
  }

  assert.equal(state.answer, "RAG answer");
  assert.equal(state.steps.length, 1);
  assert.deepEqual(state.sources, [{source: "rag.md"}]);
  assert.equal(state.terminationReason, "final_answer");
  assert.equal(state.selectedTool, "retrieval_tool");
  assert.equal(state.completed, true);
});

test("publishes partial state before the stream closes", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(value) {
      controller = value;
    },
  });
  let state = createInitialAgentStreamState();
  let resolveStep!: () => void;
  const stepSeen = new Promise<void>((resolve) => {
    resolveStep = resolve;
  });

  const reading = readAgentStream(stream, (event) => {
    state = reduceAgentStreamState(state, event);
    if (event.type === "step") resolveStep();
  });

  controller.enqueue(
    encoder.encode(
      line({
        version: 1,
        type: "step",
        data: {
          round: 1,
          status: "tool_executed",
          tool_name: "retrieval_tool",
          tool_args: {question: "RAG?"},
          tool_status: "success",
        },
      }),
    ),
  );
  await stepSeen;

  assert.equal(state.steps.length, 1);
  assert.equal(state.completed, false);

  controller.enqueue(encoder.encode(line(answerEvent) + line(doneEvent)));
  controller.close();
  await reading;

  assert.equal(state.answer, "你好");
  assert.equal(state.completed, true);
});

test("uses the BFF error message instead of the raw status code", async () => {
  const response = Response.json(
    {error: "Agent 服务暂时不可用"},
    {status: 502},
  );

  assert.equal(await readAgentErrorMessage(response), "Agent 服务暂时不可用");
});

test("falls back to the status code for non-JSON error bodies", async () => {
  const html = new Response("<html>502 Bad Gateway</html>", {status: 502});
  const missingField = Response.json({detail: "boom"}, {status: 500});

  assert.equal(await readAgentErrorMessage(html), "后端返回 502");
  assert.equal(await readAgentErrorMessage(missingField), "后端返回 500");
});

test("stall watchdog fires only after the configured silence", async () => {
  let stalls = 0;
  const watchdog = createStallWatchdog(() => {
    stalls += 1;
  }, 20);

  watchdog.reset();
  await new Promise((resolve) => setTimeout(resolve, 10));
  watchdog.reset();
  await new Promise((resolve) => setTimeout(resolve, 15));

  assert.equal(stalls, 0);

  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(stalls, 1);
});

test("cleared stall watchdog never fires", async () => {
  let stalls = 0;
  const watchdog = createStallWatchdog(() => {
    stalls += 1;
  }, 10);

  watchdog.reset();
  watchdog.clear();
  await new Promise((resolve) => setTimeout(resolve, 30));

  assert.equal(stalls, 0);
});

test("maps a broken connection to the interrupted-stream error", async () => {
  let reads = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      reads += 1;
      if (reads === 1) {
        controller.enqueue(encoder.encode(line(answerEvent)));
        return;
      }
      controller.error(new TypeError("terminated"));
    },
  });

  const seen: AgentStreamEvent[] = [];
  const error = await readAgentStream(stream, (event) => {
    seen.push(event);
  }).catch((err: unknown) => err);

  assert.ok(error instanceof AgentStreamProtocolError);
  assert.equal(error.code, "stream_ended_early");
  assert.equal(seen.length, 1);
});
