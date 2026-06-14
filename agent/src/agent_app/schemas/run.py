from typing import Any

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    question: str


class AgentRunResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    selected_tool: str
    tool_status: str
    tool_output: dict[str, Any]
    trace: list[dict[str, Any]]
    termination_reason: str = "single_step"
    steps: list[dict[str, Any]] = Field(default_factory=list)


class AgentToolResponse(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
