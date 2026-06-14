from agent_app.orchestration.executor import ToolResult, execute_plan, run_tool
from agent_app.orchestration.planner import AgentPlan
from agent_app.tools.registry import ToolDefinition, get_tool


def test_run_tool_runs_retrieval_tool(monkeypatch) -> None:
    expected = {
        "answer": "RAG answer",
        "sources": [],
        "trace": [],
    }

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        lambda question: expected,
    )

    result = run_tool(
        "retrieval_tool",
        tool_input={"question": "What is RAG?"},
    )

    assert result.tool_name == "retrieval_tool"
    assert result.status == "success"
    assert result.output == expected
    assert result.attempts == [
        {
            "attempt": 1,
            "status": "success",
        }
    ]


def test_execute_plan_delegates_to_run_tool(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_run_tool(
        tool_name: str,
        tool_input: dict | None = None,
    ) -> ToolResult:
        calls.append(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
            }
        )
        return ToolResult(
            tool_name=tool_name,
            status="success",
            output={"answer": "delegated", "sources": []},
            attempts=[{"attempt": 1, "status": "success"}],
        )

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_tool",
        fake_run_tool,
    )
    plan = AgentPlan(
        tool=get_tool("retrieval_tool"),
        reason="question requires knowledge retrieval",
    )

    result = execute_plan(
        plan,
        tool_input={"question": "What is RAG?"},
    )

    assert calls == [
        {
            "tool_name": "retrieval_tool",
            "tool_input": {"question": "What is RAG?"},
        }
    ]
    assert result.output == {"answer": "delegated", "sources": []}


def test_execute_plan_runs_retrieval_tool(monkeypatch) -> None:
    plan = AgentPlan(
        tool=get_tool("retrieval_tool"),
        reason="question requires knowledge retrieval",
    )

    expected = {
        "answer": "RAG answer",
        "sources": [],
        "trace": [],
    }

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        lambda question: expected,
    )

    result = execute_plan(
        plan,
        tool_input={"question": "What is RAG?"},
    )

    assert result.tool_name == "retrieval_tool"
    assert result.status == "success"
    assert result.output == expected
    assert result.attempts == [
        {
            "attempt": 1,
            "status": "success",
        }
    ]


def test_execute_plan_runs_fallback_tool() -> None:
    plan = AgentPlan(
        tool=get_tool("fallback_tool"),
        reason="question does not require retrieval",
    )

    result = execute_plan(plan, tool_input={})

    assert result.tool_name == "fallback_tool"
    assert result.status == "success"
    assert result.output == {
        "answer": "No retrieval is needed for this question.",
        "sources": [],
    }
    assert result.attempts == [
        {
            "attempt": 1,
            "status": "success",
        }
    ]


def test_execute_plan_runs_summary_tool(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_summary_tool",
        lambda text: {"summary": "LangChain helps build LLM apps."},
    )
    plan = AgentPlan(
        tool=get_tool("summary_tool"),
        reason="question asks for summarization",
    )

    result = execute_plan(
        plan,
        tool_input={"text": "  LangChain   helps build LLM apps.  "},
    )

    assert result.tool_name == "summary_tool"
    assert result.status == "success"
    assert result.output == {
        "summary": "LangChain helps build LLM apps.",
    }
    assert result.attempts == [
        {
            "attempt": 1,
            "status": "success",
        }
    ]


def test_execute_plan_runs_question_decompose_tool(monkeypatch) -> None:
    plan = AgentPlan(
        tool=get_tool("question_decompose_tool"),
        reason="question contains comparison or multi-part intent",
    )

    def fake_retrieval_tool(question: str) -> dict:
        return {
            "answer": f"{question} answer",
            "sources": [
                {
                    "source": question,
                }
            ],
            "trace": [],
        }

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        fake_retrieval_tool,
    )

    result = execute_plan(
        plan,
        tool_input={
            "question": "LangChain 和 LlamaIndex 分别适合做什么？",
        },
    )

    assert result.tool_name == "question_decompose_tool"
    assert result.status == "success"
    assert result.output == {
        "answer": (
            "1. LangChain 适合做什么？\n"
            "LangChain 适合做什么？ answer\n\n"
            "2. LlamaIndex 适合做什么？\n"
            "LlamaIndex 适合做什么？ answer"
        ),
        "sources": [
            {
                "source": "LangChain 适合做什么？",
            },
            {
                "source": "LlamaIndex 适合做什么？",
            },
        ],
        "sub_questions": [
            "LangChain 适合做什么？",
            "LlamaIndex 适合做什么？",
        ],
        "sub_results": [
            {
                "question": "LangChain 适合做什么？",
                "status": "success",
                "answer": "LangChain 适合做什么？ answer",
                "sources": [
                    {
                        "source": "LangChain 适合做什么？",
                    }
                ],
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
                "sources": [
                    {
                        "source": "LlamaIndex 适合做什么？",
                    }
                ],
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
    assert result.attempts == [
        {
            "attempt": 1,
            "status": "success",
        }
    ]


def test_execute_plan_marks_question_decompose_partial_success(
    monkeypatch,
) -> None:
    plan = AgentPlan(
        tool=get_tool("question_decompose_tool"),
        reason="question contains comparison or multi-part intent",
    )

    def partially_failing_retrieval_tool(question: str) -> dict:
        if "LlamaIndex" in question:
            raise RuntimeError("rag unavailable")

        return {
            "answer": "LangChain answer",
            "sources": [],
            "trace": [],
        }

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        partially_failing_retrieval_tool,
    )

    result = execute_plan(
        plan,
        tool_input={
            "question": "LangChain 和 LlamaIndex 分别适合做什么？",
        },
    )

    assert result.tool_name == "question_decompose_tool"
    assert result.status == "partial_success"
    assert result.output["answer"] == (
        "1. LangChain 适合做什么？\nLangChain answer"
    )
    assert result.output["sub_results"][1] == {
        "question": "LlamaIndex 适合做什么？",
        "status": "failed",
        "answer": "",
        "sources": [],
        "attempts": [
            {
                "attempt": 1,
                "status": "failed",
                "error_type": "RuntimeError",
                "error": "rag unavailable",
            }
        ],
        "error_type": "RuntimeError",
        "error": "rag unavailable",
    }
    assert result.attempts == [
        {
            "attempt": 1,
            "status": "partial_success",
        }
    ]


def test_execute_plan_returns_failed_result_for_unsupported_tool() -> None:
    plan = AgentPlan(
        tool=ToolDefinition(
            name="unknown_tool",
            description="Unknown tool.",
            input_schema={
                "type": "object",
                "properties": {},
            },
            output_schema={
                "type": "object",
                "properties": {},
            },
        ),
        reason="unsupported test case",
    )

    result = execute_plan(plan, tool_input={})

    assert result.tool_name == "unknown_tool"
    assert result.status == "failed"
    assert result.output == {
        "error": "Unsupported tool: unknown_tool",
    }
    assert result.attempts == [
        {
            "attempt": 1,
            "status": "failed",
            "error": "Unsupported tool: unknown_tool",
        }
    ]


def test_execute_plan_does_not_retry_retrieval_tool(monkeypatch) -> None:
    plan = AgentPlan(
        tool=get_tool("retrieval_tool"),
        reason="question requires knowledge retrieval",
    )
    calls = {"count": 0}

    def raise_error(question: str) -> None:
        calls["count"] += 1
        raise RuntimeError("rag unavailable")

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        raise_error,
    )

    result = execute_plan(
        plan,
        tool_input={"question": "What is RAG?"},
    )

    assert result.tool_name == "retrieval_tool"
    assert result.status == "failed"
    assert result.output == {
        "error_type": "RuntimeError",
        "error": "rag unavailable",
    }
    assert result.attempts == [
        {
            "attempt": 1,
            "status": "failed",
            "error_type": "RuntimeError",
            "error": "rag unavailable",
        },
    ]
    assert calls["count"] == 1


def test_execute_plan_returns_failed_result_when_retrieval_tool_fails(
    monkeypatch,
) -> None:
    plan = AgentPlan(
        tool=get_tool("retrieval_tool"),
        reason="question requires knowledge retrieval",
    )

    def raise_error(question: str) -> None:
        raise RuntimeError("rag unavailable")

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_retrieval_tool",
        raise_error,
    )

    result = execute_plan(
        plan,
        tool_input={"question": "What is RAG?"},
    )

    assert result.tool_name == "retrieval_tool"
    assert result.status == "failed"
    assert result.output == {
        "error_type": "RuntimeError",
        "error": "rag unavailable",
    }
    assert result.attempts == [
        {
            "attempt": 1,
            "status": "failed",
            "error_type": "RuntimeError",
            "error": "rag unavailable",
        },
    ]


def test_execute_plan_returns_failed_result_when_summary_tool_fails(
    monkeypatch,
) -> None:
    plan = AgentPlan(
        tool=get_tool("summary_tool"),
        reason="question asks for summarization",
    )

    def raise_summary_error(text: str):
        raise RuntimeError("summary model unavailable")

    monkeypatch.setattr(
        "agent_app.orchestration.executor.run_summary_tool",
        raise_summary_error,
    )

    result = execute_plan(
        plan,
        tool_input={"text": "LangChain helps build LLM apps."},
    )

    assert result.tool_name == "summary_tool"
    assert result.status == "failed"
    assert result.output == {
        "error_type": "RuntimeError",
        "error": "summary model unavailable",
    }
    assert result.attempts == [
        {
            "attempt": 1,
            "status": "failed",
            "error_type": "RuntimeError",
            "error": "summary model unavailable",
        }
    ]
