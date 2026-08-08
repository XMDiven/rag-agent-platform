import assert from "node:assert/strict";
import test from "node:test";

import {POST} from "../app/api/agent/stream/route.ts";

const originalFetch = globalThis.fetch;

function validRequest(): Request {
  return new Request("http://localhost/api/agent/stream", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({question: "LangChain 是什么？"}),
  });
}

test.afterEach(() => {
  globalThis.fetch = originalFetch;
  delete process.env.AGENT_STREAM_API_URL;
});

test("forwards a valid question to the configured streaming Agent API", async () => {
  process.env.AGENT_STREAM_API_URL =
    "http://agent.example/agent/run/stream";
  let receivedUrl = "";
  let receivedInit: RequestInit | undefined;

  globalThis.fetch = async (input, init) => {
    receivedUrl = String(input);
    receivedInit = init;
    return new Response(
      '{"version":1,"type":"done","data":{"termination_reason":"final_answer","selected_tool":"fallback_tool","tool_status":"success"}}\n',
      {
        status: 202,
        headers: {"Content-Type": "application/x-ndjson"},
      },
    );
  };

  const response = await POST(validRequest());

  assert.equal(receivedUrl, "http://agent.example/agent/run/stream");
  assert.equal(receivedInit?.method, "POST");
  assert.equal(receivedInit?.cache, "no-store");
  assert.deepEqual(JSON.parse(String(receivedInit?.body)), {
    question: "LangChain 是什么？",
  });
  assert.equal(response.status, 202);
  assert.match(response.headers.get("content-type") ?? "", /ndjson/);
  assert.equal(response.headers.get("cache-control"), "no-cache");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
});

test("rejects a blank question without calling upstream", async () => {
  let fetchCalled = false;
  globalThis.fetch = async () => {
    fetchCalled = true;
    return Response.json({});
  };

  const response = await POST(
    new Request("http://localhost/api/agent/stream", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: "   "}),
    }),
  );

  assert.equal(fetchCalled, false);
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), {error: "请输入有效问题"});
});

test("returns a stable 502 when streaming Agent API is unavailable", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("network unavailable");
  };

  const response = await POST(validRequest());

  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), {error: "Agent 服务暂时不可用"});
});

test("re-streams the first upstream chunk before upstream closes", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const encoder = new TextEncoder();
  const upstreamBody = new ReadableStream<Uint8Array>({
    start(value) {
      controller = value;
    },
  });

  globalThis.fetch = async () =>
    new Response(upstreamBody, {
      headers: {"Content-Type": "application/x-ndjson"},
    });

  const response = await POST(validRequest());
  const reader = response.body!.getReader();
  const firstLine =
    '{"version":1,"type":"answer_delta","data":{"text":"A"}}\n';
  controller.enqueue(encoder.encode(firstLine));

  const first = await reader.read();

  assert.equal(new TextDecoder().decode(first.value), firstLine);
  controller.close();
});

test("forwards a sanitized request id upstream and back to the browser", async () => {
  const seen: Array<string | null> = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (_url: string, init: RequestInit) => {
    const headers = new Headers(init.headers);
    seen.push(headers.get("x-request-id"));
    return new Response("ok", {
      status: 200,
      headers: {"Content-Type": "application/x-ndjson"},
    });
  }) as typeof fetch;

  try {
    const forwarded = await POST(
      new Request("http://localhost:3000/api/agent/stream", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-request-id": "trace-1",
        },
        body: JSON.stringify({question: "什么是 RAG？"}),
      }),
    );
    assert.equal(seen[0], "trace-1");
    assert.equal(forwarded.headers.get("x-request-id"), "trace-1");

    const forged = await POST(
      new Request("http://localhost:3000/api/agent/stream", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-request-id": "bad id with spaces",
        },
        body: JSON.stringify({question: "什么是 RAG？"}),
      }),
    );
    assert.notEqual(seen[1], "bad id with spaces");
    assert.match(String(seen[1]), /^[A-Za-z0-9._-]{1,64}$/);
    assert.equal(forged.headers.get("x-request-id"), seen[1]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
