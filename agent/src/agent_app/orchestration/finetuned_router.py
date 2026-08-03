"""Optional tool selection through a fine-tuned router service.

This is an alternative to `select_tool_with_llm`, enabled by `ROUTER_BACKEND`
and off by default. It calls the router over HTTP and translates its decision
into the platform's existing `ToolSelection`, so `run_tool` dispatch is
untouched.

The router is offered only the three knowledge tools this platform dispatches.
Anything it returns that cannot be dispatched -- a non-tool decision, a tool
outside that menu, a transport error -- raises, and the planner degrades to
the LLM path. Improvising here would let an optional component change the
agent's behaviour in ways its own tests never covered.

Uses `urllib` rather than an HTTP client library: one POST does not justify a
new runtime dependency in this repository.
"""

import json
import urllib.request
from typing import Any

from agent_app.orchestration.tool_selector import ToolSelection
from agent_app.tools import get_tool

KNOWLEDGE_TOOL_NAMES = ("retrieval_tool", "summary_tool", "question_decompose_tool")

DEFAULT_TIMEOUT_SECONDS = 30.0


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def select_tool_with_finetuned_router(
    question: str,
    base_url: str | None = None,
) -> ToolSelection:
    """Ask the fine-tuned router which knowledge tool to run."""
    from rag_app.config.config import FINETUNED_ROUTER_URL

    url = f"{(base_url or FINETUNED_ROUTER_URL).rstrip('/')}/v1/route"
    body = _post_json(
        url,
        {
            "messages": [{"role": "user", "content": question}],
            "tools": list(KNOWLEDGE_TOOL_NAMES),
        },
    )

    decision = body["decision"]
    if decision.get("action") != "tool_call":
        raise ValueError(f"router returned {decision.get('action')!r}, not a tool call")

    call = decision["tool_call"]
    name = call["name"]
    if name not in KNOWLEDGE_TOOL_NAMES:
        raise ValueError(f"router returned off-menu tool {name!r}")

    revision = body.get("adapter_revision", "unknown")

    return ToolSelection(
        tool=get_tool(name),
        tool_args=call.get("arguments") or {},
        reason=f"finetuned router selected tool (adapter {revision})",
    )
