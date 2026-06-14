from dataclasses import dataclass
from typing import Any

from agent_app.orchestration.planner import AgentPlan
from agent_app.tools.question_decompose import run_question_decompose_tool
from agent_app.tools.retrieval import run_retrieval_tool
from agent_app.tools.summary import run_summary_tool


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    status: str
    output: Any
    attempts: list[dict[str, Any]]


def successful_tool_result(tool_name: str, output: Any) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        status="success",
        output=output,
        attempts=[{"attempt": 1, "status": "success"}],
    )


def failed_tool_result(tool_name: str, error: Exception) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        status="failed",
        output={
            "error_type": type(error).__name__,
            "error": str(error),
        },
        attempts=[
            {
                "attempt": 1,
                "status": "failed",
                "error_type": type(error).__name__,
                "error": str(error),
            }
        ],
    )


def normalize_output(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        return output

    return {
        "result": output,
    }


def run_decomposed_retrieval(question: str) -> dict[str, Any]:
    decomposition = run_question_decompose_tool(question)
    sub_questions = decomposition.get("sub_questions", [])

    sub_results: list[dict[str, Any]] = []
    all_sources: list[dict[str, Any]] = []
    answer_parts: list[str] = []

    for index, sub_question in enumerate(sub_questions, start=1):
        try:
            retrieval_output = normalize_output(
                run_retrieval_tool(str(sub_question))
            )
            status = "success"
            attempts = [
                {
                    "attempt": 1,
                    "status": "success",
                }
            ]
        except Exception as error:
            retrieval_output = {
                "error_type": type(error).__name__,
                "error": str(error),
            }
            status = "failed"
            attempts = [
                {
                    "attempt": 1,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            ]

        answer = retrieval_output.get("answer", "")
        sources = retrieval_output.get("sources", [])
        normalized_sources = sources if isinstance(sources, list) else []

        all_sources.extend(normalized_sources)

        sub_result = {
            "question": str(sub_question),
            "status": status,
            "answer": answer if isinstance(answer, str) else "",
            "sources": normalized_sources,
            "attempts": attempts,
        }

        if status != "success":
            sub_result["error_type"] = retrieval_output.get("error_type", "")
            sub_result["error"] = retrieval_output.get("error", "")

        sub_results.append(sub_result)

        if isinstance(answer, str) and answer:
            answer_parts.append(f"{index}. {sub_question}\n{answer}")

    return {
        "answer": "\n\n".join(answer_parts),
        "sources": all_sources,
        "sub_questions": [str(item) for item in sub_questions],
        "sub_results": sub_results,
        "reason": decomposition.get("reason"),
        "decomposition_strategy": decomposition.get("decomposition_strategy"),
    }


def run_tool(
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
) -> ToolResult:
    if tool_name == "retrieval_tool":
        question = str((tool_input or {}).get("question", ""))
        try:
            output = run_retrieval_tool(question)
        except Exception as error:
            return ToolResult(
                tool_name=tool_name,
                status="failed",
                output={
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                attempts=[
                    {
                        "attempt": 1,
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                ],
            )

        return ToolResult(
            tool_name=tool_name,
            status="success",
            output=output,
            attempts=[
                {
                    "attempt": 1,
                    "status": "success",
                }
            ],
        )

    if tool_name == "fallback_tool":
        return ToolResult(
            tool_name=tool_name,
            status="success",
            output={
                "answer": "No retrieval is needed for this question.",
                "sources": [],
            },
            attempts=[
                {
                    "attempt": 1,
                    "status": "success",
                }
            ],
        )

    if tool_name == "summary_tool":
        text = str((tool_input or {}).get("text", ""))

        try:
            output = run_summary_tool(text)
        except Exception as error:
            return ToolResult(
                tool_name=tool_name,
                status="failed",
                output={
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                attempts=[
                    {
                        "attempt": 1,
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                ],
            )

        return ToolResult(
            tool_name=tool_name,
            status="success",
            output=output,
            attempts=[
                {
                    "attempt": 1,
                    "status": "success",
                }
            ],
        )

    if tool_name == "question_decompose_tool":
        question = str((tool_input or {}).get("question", ""))
        output = run_decomposed_retrieval(question)
        sub_results = output.get("sub_results", [])
        failed_sub_results = [
            sub_result
            for sub_result in sub_results
            if isinstance(sub_result, dict)
            and sub_result.get("status") != "success"
        ]
        status = "success"

        if failed_sub_results and len(failed_sub_results) == len(sub_results):
            status = "failed"
        elif failed_sub_results:
            status = "partial_success"

        return ToolResult(
            tool_name=tool_name,
            status=status,
            output=output,
            attempts=[
                {
                    "attempt": 1,
                    "status": status,
                }
            ],
        )

    return ToolResult(
        tool_name=tool_name,
        status="failed",
        output={
            "error": f"Unsupported tool: {tool_name}",
        },
        attempts=[
            {
                "attempt": 1,
                "status": "failed",
                "error": f"Unsupported tool: {tool_name}",
            }
        ],
    )


def execute_plan(
    plan: AgentPlan,
    tool_input: dict[str, Any] | None = None,
) -> ToolResult:
    return run_tool(
        tool_name=plan.tool.name,
        tool_input=tool_input,
    )
