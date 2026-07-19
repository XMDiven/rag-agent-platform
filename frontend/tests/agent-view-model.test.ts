import assert from "node:assert/strict";
import test from "node:test";

import {buildAgentStepViewModel} from "../app/agent-view-model.ts";

test("builds display values for a tool execution round", () => {
  const result = buildAgentStepViewModel({
    round: 1,
    status: "tool_executed",
    tool_name: "retrieval_tool",
    tool_args: {query: "LangChain 是什么？"},
    tool_status: "success",
  });

  assert.deepEqual(result, {
    roundLabel: "第 1 轮",
    statusLabel: "tool executed",
    toolLabel: "retrieval_tool",
    argsLabel: '{"query":"LangChain 是什么？"}',
    toolStatusLabel: "success",
  });
});

test("labels a final answer round without empty tool metadata", () => {
  const result = buildAgentStepViewModel({
    round: 2,
    status: "final_answer",
    tool_name: null,
  });

  assert.deepEqual(result, {
    roundLabel: "第 2 轮",
    statusLabel: "final answer",
    toolLabel: "final answer",
    argsLabel: null,
    toolStatusLabel: null,
  });
});
