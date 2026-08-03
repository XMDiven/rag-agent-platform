"""ROUTER_BACKEND selects the planner's tool source and must never be a cliff.

Default behaviour is unchanged. With the switch on, a failing router degrades
to the existing LLM path rather than to an error, and the degradation is
logged so an operator can tell the two backends apart in production.
"""

import logging

import pytest
from rag_app.config import config

from agent_app.orchestration import planner
from agent_app.orchestration.planner import plan_tool
from agent_app.orchestration.tool_selector import ToolSelection
from agent_app.tools import get_tool


def selection(name: str, reason: str) -> ToolSelection:
    return ToolSelection(tool=get_tool(name), tool_args={"question": "q"}, reason=reason)


@pytest.fixture
def llm_selects_retrieval(monkeypatch):
    monkeypatch.setattr(
        planner,
        "select_tool_with_llm",
        lambda question: selection("retrieval_tool", "llm selected"),
    )


def test_default_backend_leaves_the_llm_path_untouched(monkeypatch, llm_selects_retrieval):
    monkeypatch.setattr(config, "ROUTER_BACKEND", "llm")

    def must_not_run(question, base_url=None):
        raise AssertionError("finetuned router called while backend is llm")

    monkeypatch.setattr(planner, "select_tool_with_finetuned_router", must_not_run)

    plan = plan_tool(question_type="factual", question="q")
    assert plan.reason == "llm selected"


def test_finetuned_backend_uses_the_router(monkeypatch, llm_selects_retrieval):
    monkeypatch.setattr(config, "ROUTER_BACKEND", "finetuned")
    monkeypatch.setattr(
        planner,
        "select_tool_with_finetuned_router",
        lambda question, base_url=None: selection("summary_tool", "finetuned router selected"),
    )

    plan = plan_tool(question_type="factual", question="q")
    assert plan.tool.name == "summary_tool"
    assert plan.reason == "finetuned router selected"


def test_a_failing_router_degrades_to_the_llm_backend(monkeypatch, caplog, llm_selects_retrieval):
    monkeypatch.setattr(config, "ROUTER_BACKEND", "finetuned")

    def unreachable(question, base_url=None):
        raise TimeoutError("router unreachable")

    monkeypatch.setattr(planner, "select_tool_with_finetuned_router", unreachable)

    with caplog.at_level(logging.WARNING):
        plan = plan_tool(question_type="factual", question="q")

    assert plan.reason == "llm selected"
    assert "router_backend" in caplog.text
    assert "TimeoutError" in caplog.text


def test_both_backends_failing_still_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(config, "ROUTER_BACKEND", "finetuned")

    def unreachable(question, base_url=None):
        raise TimeoutError("router unreachable")

    def llm_down(question):
        raise RuntimeError("llm down")

    monkeypatch.setattr(planner, "select_tool_with_finetuned_router", unreachable)
    monkeypatch.setattr(planner, "select_tool_with_llm", llm_down)

    plan = plan_tool(question_type="factual", question="q")
    assert plan.tool.name == "retrieval_tool"
    assert plan.reason == "question requires knowledge retrieval"


def test_empty_questions_never_reach_either_backend(monkeypatch):
    monkeypatch.setattr(config, "ROUTER_BACKEND", "finetuned")

    def must_not_run(*args, **kwargs):
        raise AssertionError("backend called for an empty question")

    monkeypatch.setattr(planner, "select_tool_with_finetuned_router", must_not_run)
    monkeypatch.setattr(planner, "select_tool_with_llm", must_not_run)

    plan = plan_tool(question_type="empty", question="")
    assert plan.tool.name == "fallback_tool"
