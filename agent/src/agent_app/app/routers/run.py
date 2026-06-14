from typing import Any

from fastapi import APIRouter

from agent_app.schemas.run import AgentRunRequest, AgentRunResponse
from agent_app.service import run_agent

router = APIRouter()


def normalize_tool_output(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        return output

    return {
        "result": output,
    }


def extract_answer(tool_output: dict[str, Any]) -> str:
    answer = tool_output.get("answer")
    if isinstance(answer, str):
        return answer

    summary = tool_output.get("summary")
    if isinstance(summary, str):
        return summary

    error = tool_output.get("error")
    if isinstance(error, str):
        return error

    return ""


def extract_sources(tool_output: dict[str, Any]) -> list[dict[str, Any]]:
    sources = tool_output.get("sources")
    if isinstance(sources, list):
        return sources

    return []


@router.post("/agent/run", response_model=AgentRunResponse)
def run_agent_endpoint(request: AgentRunRequest) -> AgentRunResponse:
    result = run_agent(request.question)

    tool_output = normalize_tool_output(result.tool_result.output)

    return AgentRunResponse(
        answer=extract_answer(tool_output),
        sources=extract_sources(tool_output),
        selected_tool=result.plan.tool.name,
        tool_status=result.tool_result.status,
        tool_output=tool_output,
        trace=result.trace,
    )
