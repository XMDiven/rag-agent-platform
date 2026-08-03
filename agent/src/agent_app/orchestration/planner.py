import logging
from dataclasses import dataclass
from typing import Any

from rag_app.config import config

from agent_app.orchestration.finetuned_router import (
    select_tool_with_finetuned_router,
)
from agent_app.orchestration.tool_selector import select_tool_with_llm
from agent_app.tools.question_decompose import has_decomposition_signal
from agent_app.tools.registry import ToolDefinition, get_tool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentPlan:
    tool: ToolDefinition
    reason: str
    tool_args: dict[str, Any] | None = None


def plan_tool_by_rules(question_type: str, question: str = "") -> AgentPlan:
    if question_type == "empty":
        return AgentPlan(
            tool=get_tool("fallback_tool"),
            reason="question does not require retrieval",
        )

    if has_decomposition_signal(question):
        return AgentPlan(
            tool=get_tool("question_decompose_tool"),
            reason="question contains comparison or multi-part intent",
        )

    if question_type == "summary":
        return AgentPlan(
            tool=get_tool("summary_tool"),
            reason="question asks for summarization",
        )

    return AgentPlan(
        tool=get_tool("retrieval_tool"),
        reason="question requires knowledge retrieval",
    )


def _select_tool(question: str) -> "ToolSelection":
    """Pick the configured backend, degrading to the LLM path on any failure.

    The switch is read per call rather than at import so it can be changed
    without a restart, and so tests can exercise both paths. A router failure
    must never surface to the caller: this backend is optional and the LLM path
    is the behaviour the platform's own tests cover.
    """
    if config.ROUTER_BACKEND == "finetuned":
        try:
            return select_tool_with_finetuned_router(question)
        except Exception as exc:
            logger.warning(
                "agent.router_backend degrade backend=finetuned error_type=%s",
                type(exc).__name__,
            )

    return select_tool_with_llm(question=question)


def plan_tool(question_type: str, question: str = "") -> AgentPlan:
    if question_type == "empty":
        return plan_tool_by_rules(question_type=question_type, question=question)

    try:
        selection = _select_tool(question)
    except Exception as exc:
        logger.warning(
            "agent.tool_selection fallback question_type=%s error_type=%s",
            question_type,
            type(exc).__name__,
        )
        return plan_tool_by_rules(question_type=question_type, question=question)

    return AgentPlan(
        tool=selection.tool,
        reason=selection.reason,
        tool_args=selection.tool_args,
    )
