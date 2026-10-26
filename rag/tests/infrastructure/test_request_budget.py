import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from rag_app.infrastructure.request_budget import (
    BudgetExceeded, RequestBudget, check_request_budget, get_request_budget,
    request_budget_scope,
)


def test_configured_per_call_cap_can_be_increased():
    budget = RequestBudget(max_tokens_per_call=2048, max_output_tokens=6000)
    assert budget.reserve({}).allocation == 2048
    assert not budget.can_start_tool(1)  # 3952 cannot hold two 2048 allocations


def test_scope_resets_and_check_allows_exact_consumption():
    assert get_request_budget() is None
    check_request_budget()
    budget = RequestBudget(max_calls=1, max_output_tokens=4)
    with request_budget_scope(budget):
        assert get_request_budget() is budget
        lease = budget.reserve({'messages': []})
        assert lease.allocation == 4
        lease.settle(4)
        assert budget.check() > 0
        with pytest.raises(BudgetExceeded, match='model_calls_exhausted'):
            budget.reserve({'messages': []})
    assert get_request_budget() is None


def test_context_counts_canonical_utf8_and_rejects_before_reserving():
    body = {'messages': [{'content': '中文'}], 'tools': []}
    size = len(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
    budget = RequestBudget(max_request_context_bytes=size, max_context_bytes=size)
    budget.reserve(body).settle(0)
    assert budget.to_dict()['used_context_bytes'] == size
    with pytest.raises(BudgetExceeded, match='context_budget_exhausted'):
        budget.reserve(body)
    assert budget.to_dict()['used_calls'] == 1


def test_deadline_and_stop_are_sticky():
    now = [0.0]
    budget = RequestBudget(timeout_seconds=2, clock=lambda: now[0])
    now[0] = 2
    with pytest.raises(BudgetExceeded, match='task_timeout'):
        budget.check()
    budget.stop('request_finished')
    assert budget.to_dict()['violation_code'] == 'task_timeout'


def test_tool_preflight_preserves_final_call_and_output_allocation():
    budget = RequestBudget(max_calls=3, max_output_tokens=2048)
    assert budget.can_start_tool(1)
    assert not budget.can_start_tool(2)
    budget.reserve({}).settle(1024)
    assert not budget.can_start_tool(1)


def test_reservations_are_thread_safe_and_missing_usage_keeps_allocation():
    budget = RequestBudget(max_calls=6, max_output_tokens=10000)
    def reserve(_):
        try:
            budget.reserve({})
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(reserve, range(30))) == 6
    assert budget.remaining_calls == 0
    assert budget.snapshot()['used_output_tokens'] == 6144


def test_limits_are_read_only_and_small_requested_cap_is_respected():
    budget = RequestBudget()
    with pytest.raises(AttributeError):
        budget.max_calls = 20
    assert budget.reserve({'max_tokens': 3}).allocation == 3
