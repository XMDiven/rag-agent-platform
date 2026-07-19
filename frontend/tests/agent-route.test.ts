import assert from "node:assert/strict";
import test from "node:test";

import {POST} from "../app/api/agent/route.ts";

const originalFetch = globalThis.fetch;

test.afterEach(() => {
  globalThis.fetch = originalFetch;
  delete process.env.AGENT_API_URL;
});

test("forwards a valid question to the configured Agent API", async () => {
  process.env.AGENT_API_URL = "http://agent.example/agent/run";
  const upstreamBody = {
    answer: "LangChain focuses on orchestration.",
    sources: [],
    selected_tool: "retrieval_tool",
    tool_status: "success",
    tool_output: {},
    trace: [],
    termination_reason: "final_answer",
    steps: [],
  };
  let receivedUrl = "";
  let receivedInit: RequestInit | undefined;

  globalThis.fetch = async (input, init) => {
    receivedUrl = String(input);
    receivedInit = init;
    return Response.json(upstreamBody, {status: 202});
  };

  const response = await POST(
    new Request("http://localhost/api/agent", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: "LangChain 是什么？"}),
    }),
  );

  assert.equal(receivedUrl, "http://agent.example/agent/run");
  assert.equal(receivedInit?.method, "POST");
  assert.equal(receivedInit?.cache, "no-store");
  assert.deepEqual(JSON.parse(String(receivedInit?.body)), {
    question: "LangChain 是什么？",
  });
  assert.equal(response.status, 202);
  assert.deepEqual(await response.json(), upstreamBody);
});

test("rejects a blank question without calling the Agent API", async () => {
  let fetchCalled = false;
  globalThis.fetch = async () => {
    fetchCalled = true;
    return Response.json({});
  };

  const response = await POST(
    new Request("http://localhost/api/agent", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: "   "}),
    }),
  );

  assert.equal(fetchCalled, false);
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), {error: "请输入有效问题"});
});

test("rejects malformed JSON without calling the Agent API", async () => {
  let fetchCalled = false;
  globalThis.fetch = async () => {
    fetchCalled = true;
    return Response.json({});
  };

  const response = await POST(
    new Request("http://localhost/api/agent", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "not-json",
    }),
  );

  assert.equal(fetchCalled, false);
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), {error: "请输入有效问题"});
});

test("returns a stable 502 response when the Agent API is unavailable", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("network unavailable");
  };

  const response = await POST(
    new Request("http://localhost/api/agent", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: "Qdrant 是什么？"}),
    }),
  );

  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), {error: "Agent 服务暂时不可用"});
});
