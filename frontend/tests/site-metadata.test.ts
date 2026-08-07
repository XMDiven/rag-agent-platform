import assert from "node:assert/strict";
import test from "node:test";

import { documentLanguage, siteMetadata } from "../app/site-metadata.ts";

test("publishes project-specific Chinese page metadata", () => {
  assert.equal(siteMetadata.title, "RAG + Agent 智能问答平台");
  assert.equal(
    siteMetadata.description,
    "基于 FastAPI、LangChain、Qdrant 与 Redis 的可追溯流式智能问答平台",
  );
  assert.equal(documentLanguage, "zh-CN");
});
