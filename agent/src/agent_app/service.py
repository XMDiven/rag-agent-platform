import logging

from dataclasses import dataclass
from typing import Any

from agent_app.tools import get_tool
from rag_app.infrastructure.llm_client import get_client
from rag_app.retrieval.query_analyzer import analyze_query, QueryAnalysis

from agent_app.orchestration.executor import ToolResult, execute_plan , run_tool
from agent_app.orchestration.planner import AgentPlan, plan_tool
from agent_app.orchestration.state import AgentState
from agent_app.orchestration.loop import run_agent_loop, LoopResult

AGENT_MAX_STEPS = 4
logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class AgentRunResult:
    plan: AgentPlan
    tool_result: ToolResult
    trace: list[dict[str, Any]]


def build_analysis_trace(analysis: QueryAnalysis) -> dict[str, Any]:
    return {
        "step": "analyze_question",
        "status": "completed",
        "detail": {
            "needs_retrieval": analysis.needs_retrieval,
            "question_type": analysis.question_type,
            "reason": analysis.reason,
        },
    }


def run_agent_once(question: str , analysis: QueryAnalysis) -> AgentRunResult:

    plan = plan_tool(
        question_type=analysis.question_type,
        question=question,
    )

    tool_input = (
        plan.tool_args
        if plan.tool_args is not None
        else {
            "question": question,
            "text": question,
        }
    )

    tool_result = execute_plan(
        plan=plan,
        tool_input=tool_input,
    )

    plan_detail: dict[str, Any] = {
        "tool_name": plan.tool.name,
        "reason": plan.reason,
    }
    if plan.tool_args is not None:
        plan_detail["tool_args"] = plan.tool_args

    execute_detail: dict[str, Any] = {
        "tool_name": tool_result.tool_name,
        "attempts": tool_result.attempts,
    }

    if (
        tool_result.tool_name == "question_decompose_tool"
        and isinstance(tool_result.output, dict)
    ):
        sub_questions = tool_result.output.get("sub_questions", [])
        execute_detail["decomposition_strategy"] = tool_result.output.get(
            "decomposition_strategy"
        )

        execute_detail["sub_question_count"] = (
            len(sub_questions) if isinstance(sub_questions, list) else 0
        )

    trace = [
        build_analysis_trace(analysis),
        {
            "step": "plan_tool",
            "status": "completed",
            "detail": plan_detail,
        },
        {
            "step": "execute_tool",
            "status": tool_result.status,
            "detail": execute_detail,
        },
    ]

    state = AgentState(
        question=question,
        analysis=analysis,
        plan=plan,
        tool_result=tool_result,
        trace=trace,
    )

    return AgentRunResult(
        plan=state.plan,
        tool_result=state.tool_result,
        trace=state.trace,
    )

def should_skip_loop(analysis: QueryAnalysis) -> bool:
    return analysis.question_type == "empty"

def try_run_loop(
    question: str,
    analysis: QueryAnalysis,
) -> LoopResult | None:
    try:
        return run_agent_loop(
            question=question,
            llm=get_client(),
            execute_tool=run_tool,
            max_steps=AGENT_MAX_STEPS,
        )
    except Exception as exc:
        logger.warning(
            "agent.loop fallback question_type=%s error_type=%s",
            analysis.question_type,
            type(exc).__name__,
        )
        return None


def build_loop_plan(loop_result: LoopResult) -> AgentPlan:
    if loop_result.tool_results:
        tool_name = loop_result.tool_results[-1].tool_name
    else:
        tool_name = "fallback_tool"

    try:
        tool = get_tool(tool_name)
    except ValueError:
        tool = get_tool("fallback_tool")

    return AgentPlan(
        tool=tool,
        reason=f"agent loop ({loop_result.termination_reason})",
    )



def build_loop_tool_result(loop_result: LoopResult) -> ToolResult:
    status = "failed" if loop_result.termination_reason == "failed" else "success"

    return ToolResult(
        tool_name="agent_loop",
        status=status,
        output={
            "answer": loop_result.answer,
            "sources": loop_result.sources,
        },
        attempts=[{"attempt": 1, "status": status}],
    )


def build_loop_trace(
    analysis: QueryAnalysis,
    loop_result: LoopResult,
)->list[dict[str, Any]]:
    return [
        build_analysis_trace(analysis),
        {
            "step": "agent_loop",
            "status": loop_result.termination_reason,
            "detail":
                {
                    "termination_reason": loop_result.termination_reason,
                    "steps": loop_result.steps,
                }
        }
    ]



def adapt_loop_result(
    analysis: QueryAnalysis,
    loop_result: LoopResult,
) -> AgentRunResult:

    plan = build_loop_plan(loop_result)
    tool_result = build_loop_tool_result(loop_result)
    trace = build_loop_trace(
        analysis=analysis,
        loop_result=loop_result,
    )

    return AgentRunResult(
        plan=plan,
        tool_result=tool_result,
        trace=trace,
    )


def run_agent(question: str) ->AgentRunResult:
    analysis = analyze_query(question)

    if should_skip_loop(analysis):
        return run_agent_once(question, analysis)

    loop_result = try_run_loop(question, analysis)

    if loop_result is None:
        return run_agent_once(question ,  analysis)

    return adapt_loop_result(analysis, loop_result)