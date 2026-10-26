"""Request-local, thread-safe model quotas; context sizes are JSON UTF-8 bytes."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import json
from threading import RLock
from time import monotonic
from typing import Any, Callable


class BudgetExceeded(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _Limits:
    timeout_seconds: float = 90
    max_calls: int = 6
    max_output_tokens: int = 4096
    max_tokens_per_call: int = 1024
    max_context_bytes: int = 120000
    max_request_context_bytes: int = 32000


class _Reservation:
    def __init__(self, budget: 'RequestBudget', allocation: int):
        self.allocation = allocation
        self._budget = budget
        self._settled = False

    def settle(self, completion_tokens: int) -> None:
        """Replace the reservation only when raw provider usage is reliable."""
        if type(completion_tokens) is not int or completion_tokens < 0:
            return
        with self._budget._lock:
            if self._settled:
                return
            self._settled = True
            self._budget._used_output_tokens += completion_tokens - self.allocation
            if completion_tokens > self.allocation:
                self._budget.stop('output_tokens_exhausted')
                self._budget.check()


class RequestBudget:
    def __init__(
        self, timeout_seconds: float = 90, max_calls: int = 6,
        max_output_tokens: int = 4096, max_tokens_per_call: int = 1024,
        max_context_bytes: int = 120000, max_request_context_bytes: int = 32000,
        clock: Callable[[], float] = monotonic,
    ):
        self._limits = _Limits(timeout_seconds, max_calls, max_output_tokens,
                               max_tokens_per_call, max_context_bytes, max_request_context_bytes)
        if any(value <= 0 for value in asdict(self._limits).values()):
            raise ValueError('request budget limits must be positive')
        self._clock = clock
        self._started = clock()
        self._lock = RLock()
        self._used_calls = 0
        self._used_output_tokens = 0
        self._used_context_bytes = 0
        self._violation_code: str | None = None

    @property
    def timeout_seconds(self):
        return self._limits.timeout_seconds

    @property
    def max_calls(self):
        return self._limits.max_calls

    @property
    def max_output_tokens(self):
        return self._limits.max_output_tokens

    @property
    def max_tokens_per_call(self):
        return self._limits.max_tokens_per_call

    @property
    def max_context_bytes(self):
        return self._limits.max_context_bytes

    @property
    def max_request_context_bytes(self):
        return self._limits.max_request_context_bytes

    def stop(self, code: str) -> None:
        with self._lock:
            if self._violation_code is None:
                self._violation_code = code

    def check(self) -> float:
        """Check time and sticky termination, allowing exactly consumed quotas."""
        with self._lock:
            remaining = self.timeout_seconds - (self._clock() - self._started)
            if self._violation_code is None and remaining <= 0:
                self._violation_code = 'task_timeout'
            if self._violation_code is not None:
                raise BudgetExceeded(self._violation_code)
            return remaining

    @property
    def remaining_calls(self) -> int:
        with self._lock:
            return max(0, self.max_calls - self._used_calls)

    def can_start_tool(self, model_calls: int) -> bool:
        """Keep one model call and one full output allocation for the answer."""
        with self._lock:
            self.check()
            allocation = self.max_tokens_per_call
            return (model_calls >= 0 and self.remaining_calls >= model_calls + 1
                    and self.max_output_tokens - self._used_output_tokens >= allocation * (model_calls + 1))

    def reserve(self, body: dict[str, Any]) -> _Reservation:
        context = {key: body[key] for key in ('messages', 'tools') if key in body}
        size = len(json.dumps(context, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':')).encode('utf-8'))
        with self._lock:
            self.check()
            for exhausted, code in (
                (self._used_calls >= self.max_calls, 'model_calls_exhausted'),
                (size > self.max_request_context_bytes or self._used_context_bytes + size > self.max_context_bytes,
                 'context_budget_exhausted'),
                (self._used_output_tokens >= self.max_output_tokens, 'output_tokens_exhausted'),
            ):
                if exhausted:
                    self.stop(code)
                    self.check()
            allocation = min(self.max_tokens_per_call, self.max_output_tokens - self._used_output_tokens)
            for key in ('max_tokens', 'max_completion_tokens'):
                requested = body.get(key)
                if type(requested) is int and requested > 0:
                    allocation = min(allocation, requested)
            self._used_calls += 1
            self._used_context_bytes += size
            self._used_output_tokens += allocation
            return _Reservation(self, allocation)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {'limits': asdict(self._limits), 'used_calls': self._used_calls,
                    'used_output_tokens': self._used_output_tokens,
                    'used_context_bytes': self._used_context_bytes,
                    'violation_code': self._violation_code}

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot()


_active_budget: ContextVar[RequestBudget | None] = ContextVar('request_budget', default=None)


def get_request_budget() -> RequestBudget | None:
    return _active_budget.get()


def check_request_budget() -> float | None:
    budget = get_request_budget()
    return budget.check() if budget is not None else None


@contextmanager
def request_budget_scope(budget: RequestBudget):
    token = _active_budget.set(budget)
    try:
        yield budget
    finally:
        _active_budget.reset(token)
