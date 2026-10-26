import asyncio
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from rag_app.infrastructure.request_budget import BudgetExceeded, RequestBudget, request_budget_scope
from agent_app import service
from agent_app.orchestration import executor, planner
from agent_app.orchestration.loop import run_agent_loop
from agent_app.orchestration.streaming import stream_agent_loop
from agent_app.orchestration.async_streaming import stream_agent_loop_async


def test_loop_failure_does_not_start_fallback(monkeypatch):
    budget = RequestBudget()
    fallback = Mock()

    def loop(**kwargs):
        budget.stop('model_calls_exhausted')
        raise RuntimeError('SDK wrapper')

    monkeypatch.setattr(service, 'get_client', lambda: Mock())
    monkeypatch.setattr(service, 'run_agent_loop', loop)
    monkeypatch.setattr(service, 'run_agent_once', fallback)
    with request_budget_scope(budget), pytest.raises(BudgetExceeded):
        service.run_agent('什么是 RAG？')
    fallback.assert_not_called()


def test_planner_does_not_swallow_budget_error(monkeypatch):
    monkeypatch.setattr(planner, '_select_tool', Mock(side_effect=BudgetExceeded('task_timeout')))
    with request_budget_scope(RequestBudget()), pytest.raises(BudgetExceeded):
        planner.plan_tool('knowledge', '什么是 RAG？')


def test_decomposition_stops_before_second_subquestion(monkeypatch):
    budget = RequestBudget()
    calls = []

    def retrieval(question):
        calls.append(question)
        budget.stop('task_timeout')
        return {'answer': 'late', 'sources': []}

    monkeypatch.setattr(executor, 'run_retrieval_tool', retrieval)
    with request_budget_scope(budget), pytest.raises(BudgetExceeded):
        executor.run_tool('question_decompose_tool', {'question': '甲和乙分别是什么？'})
    assert len(calls) == 1


def test_optional_router_is_counted_and_uses_remaining_deadline(monkeypatch):
    from agent_app.orchestration import finetuned_router
    now = [0]
    budget = RequestBudget(timeout_seconds=10, max_calls=1, clock=lambda: now[0])
    raw = Mock()
    raw.read.return_value = b'{}'
    opened = Mock()
    opened.__enter__ = Mock(return_value=raw)
    opened.__exit__ = Mock(return_value=False)
    urlopen = Mock(return_value=opened)
    monkeypatch.setattr(finetuned_router.urllib.request, 'urlopen', urlopen)
    now[0] = 8
    with request_budget_scope(budget):
        finetuned_router._post_json('http://offline.invalid/v1/route', {'messages': []})
        with pytest.raises(BudgetExceeded, match='model_calls_exhausted'):
            finetuned_router._post_json('http://offline.invalid/v1/route', {'messages': []})
    assert urlopen.call_count == 1
    assert urlopen.call_args.kwargs['timeout'] == 2
    assert budget.snapshot()['used_output_tokens'] == 1024  # no reliable usage returned


class BudgetModel:
    def __init__(self, first_tool=True):
        self.first_tool = first_tool
        self.choices = []
        self.histories = []

    def bind_tools(self, tools, tool_choice):
        self.choices.append(tool_choice)
        return self

    def invoke(self, messages):
        from rag_app.infrastructure.request_budget import get_request_budget
        self.histories.append(list(messages))
        get_request_budget().reserve({'messages': []}).settle(3)
        if len(self.histories) == 1 and self.first_tool:
            return AIMessage(content='', tool_calls=[
                {'id': 'a', 'name': 'question_decompose_tool', 'args': {'question': '甲和乙分别是什么？'}},
                {'id': 'b', 'name': 'retrieval_tool', 'args': {'question': '其他问题'}},
            ])
        return AIMessage(content='final answer')

    def stream(self, messages):
        message = self.invoke(messages)
        if message.tool_calls:
            yield AIMessageChunk(content='', tool_call_chunks=[
                {'name': call['name'], 'args': __import__('json').dumps(call['args']),
                 'id': call['id'], 'index': i, 'type': 'tool_call_chunk'}
                for i, call in enumerate(message.tool_calls)
            ])
        else:
            yield AIMessageChunk(content=message.content)

    async def astream(self, messages):
        for chunk in self.stream(messages):
            yield chunk


@pytest.mark.parametrize('mode', ['sync', 'stream', 'async'])
@pytest.mark.parametrize('max_calls,first_tool', [(3, True), (1, False)])
def test_all_loops_preserve_final_answer_capacity(mode, max_calls, first_tool):
    model = BudgetModel(first_tool)
    execute = Mock(side_effect=AssertionError('tool must be skipped'))
    budget = RequestBudget(max_calls=max_calls)
    with request_budget_scope(budget):
        if mode == 'sync':
            result = run_agent_loop('问题', model, execute)
            assert result.answer == 'final answer'
            assert result.termination_reason == 'final_answer'
        else:
            if mode == 'stream':
                events = list(stream_agent_loop('问题', model, execute))
            else:
                async def run():
                    return [e async for e in stream_agent_loop_async('问题', model, execute)]
                events = asyncio.run(run())
            assert events[-1].data.termination_reason == 'final_answer'
            assert ''.join(e.data.text for e in events if e.type == 'answer_delta') == 'final answer'
    assert model.choices[-1] == 'none'
    if first_tool:
        acknowledgments = [m.tool_call_id for m in model.histories[-1] if isinstance(m, ToolMessage)]
        assert acknowledgments == ['a', 'b']
        assert budget.snapshot()['used_calls'] == 2
    execute.assert_not_called()
