from typing import Any

import pytest
from langchain_core.messages import AIMessage
from rag_app.retrieval.query_analyzer import analyze_query

from agent_app.orchestration.tool_selector import ToolSelection
from agent_app.service import run_agent, run_agent_once
from agent_app.tools import get_tool


def patch_tool_selection(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    tool_args: dict[str, Any],
    reason: str = "llm selected tool via native tool calling",
) -> None:
    monkeypatch.setattr(
        "agent_app.orchestration.planner.select_tool_with_llm",
        lambda question: ToolSelection(
            tool=get_tool(tool_name),
            tool_args=tool_args,
            reason=reason,
        ),
    )


def run_once(question: str) -> Any:
    return run_agent_once(question, analyze_query(question))


def test_run_agent_once_uses_retrieval_tool(monkeypatch) -> None:
    patch_tool_selection(
        monkeypatch=monkeypatch,
        tool_name="retrieval_tool",
        tool_args={"question": "rewritten retrieval question"},
    )

    expected = {
        "answer": "RAG answer",
        "sources": [],
        "trace": [],
    }

    def fake_retrieval_tool(question: str) -> dict:
        assert question == "rewritten retrieval question"
        return expected

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        fake_retrieval_tool,
    )

    result = run_once("What is RAG?")

    assert result.plan.tool.name == "retrieval_tool"
    assert result.tool_result.tool_name == "retrieval_tool"
    assert result.tool_result.status == "success"
    assert result.tool_result.output == expected
    assert result.tool_result.attempts == [
        {
            "attempt": 1,
            "status": "success",
        }
    ]

    assert result.trace == [
        {
            "step": "analyze_question",
            "status": "completed",
            "detail": {
                "needs_retrieval": True,
                "question_type": "general",
                "reason": "normal knowledge question, use retrieval",
            },
        },
        {
            "step": "plan_tool",
            "status": "completed",
            "detail": {
                "tool_name": "retrieval_tool",
                "reason": "llm selected tool via native tool calling",
                "tool_args": {
                    "question": "rewritten retrieval question",
                },
            },
        },
        {
            "step": "execute_tool",
            "status": "success",
            "detail": {
                "tool_name": "retrieval_tool",
                "attempts": [
                    {
                        "attempt": 1,
                        "status": "success",
                    }
                ],
            },
        },
    ]


def test_run_agent_uses_fallback_tool_when_retrieval_is_not_needed() -> None:
    result = run_agent("")

    assert result.plan.tool.name == "fallback_tool"
    assert result.tool_result.tool_name == "fallback_tool"
    assert result.tool_result.status == "success"
    assert result.tool_result.output == {
        "answer": "No retrieval is needed for this question.",
        "sources": [],
    }
    assert result.tool_result.attempts == [
        {
            "attempt": 1,
            "status": "success",
        }
    ]
    assert result.trace == [
        {
            "step": "analyze_question",
            "status": "completed",
            "detail": {
                "needs_retrieval": False,
                "question_type": "empty",
                "reason": "empty question",
            },
        },
        {
            "step": "plan_tool",
            "status": "completed",
            "detail": {
                "tool_name": "fallback_tool",
                "reason": "question does not require retrieval",
            },
        },
        {
            "step": "execute_tool",
            "status": "success",
            "detail": {
                "tool_name": "fallback_tool",
                "attempts": [
                    {
                        "attempt": 1,
                        "status": "success",
                    }
                ],
            },
        },
    ]


def test_run_agent_once_uses_summary_tool_for_summary_question(
    monkeypatch,
) -> None:
    patch_tool_selection(
        monkeypatch=monkeypatch,
        tool_name="summary_tool",
        tool_args={"text": "请总结 LangChain 的用途"},
    )
    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_summary_tool",
        lambda text: {"summary": "LangChain 用途摘要"},
    )

    result = run_once("请总结 LangChain 的用途")

    assert result.plan.tool.name == "summary_tool"
    assert result.tool_result.tool_name == "summary_tool"
    assert result.tool_result.status == "success"
    assert result.tool_result.output == {
        "summary": "LangChain 用途摘要",
    }
    assert result.tool_result.attempts == [
        {
            "attempt": 1,
            "status": "success",
        }
    ]
    assert result.trace == [
        {
            "step": "analyze_question",
            "status": "completed",
            "detail": {
                "needs_retrieval": True,
                "question_type": "summary",
                "reason": "summary question, use retrieval",
            },
        },
        {
            "step": "plan_tool",
            "status": "completed",
            "detail": {
                "tool_name": "summary_tool",
                "reason": "llm selected tool via native tool calling",
                "tool_args": {
                    "text": "请总结 LangChain 的用途",
                },
            },
        },
        {
            "step": "execute_tool",
            "status": "success",
            "detail": {
                "tool_name": "summary_tool",
                "attempts": [
                    {
                        "attempt": 1,
                        "status": "success",
                    }
                ],
            },
        },
    ]


def test_run_agent_once_uses_question_decompose_tool_for_comparison_question(
    monkeypatch,
) -> None:
    patch_tool_selection(
        monkeypatch=monkeypatch,
        tool_name="question_decompose_tool",
        tool_args={"question": "LangChain 和 LlamaIndex 分别适合做什么？"},
    )

    def fake_retrieval_tool(question: str) -> dict:
        return {
            "answer": f"{question} answer",
            "sources": [],
            "trace": [],
        }

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        fake_retrieval_tool,
    )

    result = run_once("LangChain 和 LlamaIndex 分别适合做什么？")

    assert result.plan.tool.name == "question_decompose_tool"
    assert result.tool_result.tool_name == "question_decompose_tool"
    assert result.tool_result.status == "success"
    assert result.tool_result.output == {
        "answer": (
            "1. LangChain 适合做什么？\n"
            "LangChain 适合做什么？ answer\n\n"
            "2. LlamaIndex 适合做什么？\n"
            "LlamaIndex 适合做什么？ answer"
        ),
        "sources": [],
        "sub_questions": [
            "LangChain 适合做什么？",
            "LlamaIndex 适合做什么？",
        ],
        "sub_results": [
            {
                "question": "LangChain 适合做什么？",
                "status": "success",
                "answer": "LangChain 适合做什么？ answer",
                "sources": [],
                "attempts": [
                    {
                        "attempt": 1,
                        "status": "success",
                    }
                ],
            },
            {
                "question": "LlamaIndex 适合做什么？",
                "status": "success",
                "answer": "LlamaIndex 适合做什么？ answer",
                "sources": [],
                "attempts": [
                    {
                        "attempt": 1,
                        "status": "success",
                    }
                ],
            },
        ],
        "reason": "question contains explicit multi-part intent",
        "decomposition_strategy": "comparison",
    }
    assert result.trace[-1] == {
        "step": "execute_tool",
        "status": "success",
        "detail": {
            "tool_name": "question_decompose_tool",
            "attempts": [
                {
                    "attempt": 1,
                    "status": "success",
                }
            ],
            "decomposition_strategy": "comparison",
            "sub_question_count": 2,
        },
    }


def test_run_agent_once_marks_trace_failed_when_retrieval_tool_fails(
    monkeypatch,
) -> None:
    patch_tool_selection(
        monkeypatch=monkeypatch,
        tool_name="retrieval_tool",
        tool_args={"question": "What is RAG?"},
    )

    def raise_error(question: str) -> None:
        raise RuntimeError("rag unavailable")

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        raise_error,
    )

    result = run_once("What is RAG?")

    assert result.tool_result.status == "failed"
    assert result.tool_result.output == {
        "error_type": "RuntimeError",
        "error": "rag unavailable",
    }
    assert result.tool_result.attempts == [
        {
            "attempt": 1,
            "status": "failed",
            "error_type": "RuntimeError",
            "error": "rag unavailable",
        },
    ]
    assert result.trace[-1] == {
        "step": "execute_tool",
        "status": "failed",
        "detail": {
            "tool_name": "retrieval_tool",
            "attempts": [
                {
                    "attempt": 1,
                    "status": "failed",
                    "error_type": "RuntimeError",
                    "error": "rag unavailable",
                },
            ],
        },
    }


def test_run_agent_routes_through_loop(
    patch_loop_llm,
    make_tool_call,
    monkeypatch,
) -> None:
    patch_loop_llm(
        [
            make_tool_call("retrieval_tool", {"question": "What is RAG?"}),
            AIMessage(content="RAG grounds answers in retrieved context."),
        ]
    )
    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        lambda question: {"answer": "ctx", "sources": [{"source": "rag.md"}]},
    )

    result = run_agent("What is RAG?")

    assert result.plan.tool.name == "retrieval_tool"
    assert result.tool_result.tool_name == "agent_loop"
    assert result.tool_result.status == "success"
    assert result.tool_result.output == {
        "answer": "RAG grounds answers in retrieved context.",
        "sources": [{"source": "rag.md"}],
    }
    assert result.trace[0]["step"] == "analyze_question"
    assert result.trace[1]["step"] == "agent_loop"
    assert result.trace[1]["status"] == "final_answer"
    assert result.trace[1]["detail"]["termination_reason"] == "final_answer"
    assert result.trace[1]["detail"]["steps"][0]["tool_name"] == "retrieval_tool"


def test_run_agent_falls_back_to_single_step_when_loop_raises(
    monkeypatch,
) -> None:
    def boom() -> None:
        raise RuntimeError("llm down")

    monkeypatch.setattr("agent_app.service.get_client", boom)
    patch_tool_selection(
        monkeypatch=monkeypatch,
        tool_name="retrieval_tool",
        tool_args={"question": "What is RAG?"},
    )
    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        lambda question: {"answer": "RAG answer", "sources": [], "trace": []},
    )

    result = run_agent("What is RAG?")

    assert result.tool_result.tool_name == "retrieval_tool"
    assert result.tool_result.status == "success"
    assert [step["step"] for step in result.trace] == [
        "analyze_question",
        "plan_tool",
        "execute_tool",
    ]
