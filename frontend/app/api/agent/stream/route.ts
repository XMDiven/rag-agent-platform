import {REQUEST_ID_HEADER, resolveRequestId} from "../../../request-id.ts";

const DEFAULT_AGENT_STREAM_API_URL =
  "http://localhost:8002/agent/run/stream";

function invalidQuestionResponse(requestId: string): Response {
  return Response.json(
    {error: "请输入有效问题"},
    {status: 400, headers: {[REQUEST_ID_HEADER]: requestId}},
  );
}

export async function POST(request: Request): Promise<Response> {
  const requestId = resolveRequestId(request);
  let body: unknown;

  try {
    body = await request.json();
  } catch {
    return invalidQuestionResponse(requestId);
  }

  if (
    typeof body !== "object" ||
    body === null ||
    !("question" in body) ||
    typeof body.question !== "string" ||
    !body.question.trim()
  ) {
    return invalidQuestionResponse(requestId);
  }

  try {
    const upstreamResponse = await fetch(
      process.env.AGENT_STREAM_API_URL ??
        DEFAULT_AGENT_STREAM_API_URL,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          [REQUEST_ID_HEADER]: requestId,
        },
        body: JSON.stringify({question: body.question.trim()}),
        cache: "no-store",
      },
    );

    return new Response(upstreamResponse.body, {
      status: upstreamResponse.status,
      headers: {
        "Content-Type":
          upstreamResponse.headers.get("content-type") ??
          "application/x-ndjson",
        "Cache-Control": "no-cache",
        "X-Content-Type-Options": "nosniff",
        [REQUEST_ID_HEADER]: requestId,
      },
    });
  } catch {
    return Response.json(
      {error: "Agent 服务暂时不可用"},
      {status: 502, headers: {[REQUEST_ID_HEADER]: requestId}},
    );
  }
}
