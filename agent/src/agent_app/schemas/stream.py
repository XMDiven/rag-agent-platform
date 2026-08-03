from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field


class StepData(BaseModel):
    round: int = Field(ge=1)
    status: Literal["tool_executed", "tool_failed"]
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_status: Literal["success", "failed"]


class AnswerDeltaData(BaseModel):
    text: str = Field(min_length=1)


class SourcesData(BaseModel):
    sources: list[dict[str, Any]] = Field(default_factory=list)


class ErrorData(BaseModel):
    code: Literal[
        "agent_stream_failed",
        "mixed_model_output",
        "empty_model_output",
    ]
    message: str


class DoneData(BaseModel):
    termination_reason: Literal[
        "final_answer",
        "max_steps",
        "failed",
        "single_step",
    ]
    selected_tool: str
    tool_status: Literal["success", "failed"]


class StepEvent(BaseModel):
    version: Literal[1] = 1
    type: Literal["step"] = "step"
    data: StepData


class AnswerDeltaEvent(BaseModel):
    version: Literal[1] = 1
    type: Literal["answer_delta"] = "answer_delta"
    data: AnswerDeltaData


class SourcesEvent(BaseModel):
    version: Literal[1] = 1
    type: Literal["sources"] = "sources"
    data: SourcesData


class ErrorEvent(BaseModel):
    version: Literal[1] = 1
    type: Literal["error"] = "error"
    data: ErrorData


class DoneEvent(BaseModel):
    version: Literal[1] = 1
    type: Literal["done"] = "done"
    data: DoneData


AgentStreamEvent: TypeAlias = (
    StepEvent | AnswerDeltaEvent | SourcesEvent | ErrorEvent | DoneEvent
)


def encode_event(event: AgentStreamEvent) -> str:
    return event.model_dump_json() + "\n"
