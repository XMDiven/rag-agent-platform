"""Synchronous, CLI-only model budgets and streaming measurements.

Context size means canonical JSON UTF-8 bytes of messages + tools, never tokens.
Output is reserved before sending; failed/unknown-usage requests retain the reservation.
The deadline bounds local transport waits and detects late arrivals; it cannot cancel
work already running at the remote provider. Patch scope is process-global, so run
comparison stages serially and keep production traffic out of this context.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import json
from time import perf_counter
from typing import Any, Callable

import httpx
from langchain_core.callbacks import BaseCallbackHandler

from agent_app.prompts import AGENT_LOOP_SYSTEM_PROMPT
from rag_app.infrastructure import llm_client


@dataclass(frozen=True)
class BudgetLimits:
    max_calls: int = 6
    max_output_tokens: int = 4096
    max_tokens_per_call: int = 1024
    max_context_bytes: int = 120000
    max_request_context_bytes: int = 32000
    timeout_seconds: float = 180


JUDGE_LIMITS = BudgetLimits(1, 1024, 1024, 64000, 64000, 60)


@dataclass
class SharedQuota:
    max_calls: int = 378
    max_output_tokens: int = 65536
    max_context_bytes: int = 8 * 1024 * 1024
    used_calls: int = 0
    used_output_tokens: int = 0
    used_context_bytes: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class _Usage:
    calls: int = 0
    context_bytes: int = 0
    reserved_output_tokens: int = 0
    actual_output_tokens: int = 0
    output_tokens: int = 0


class BudgetExceeded(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_current_scope: ContextVar[Any] = ContextVar('comparison_model_budget', default=None)
_current_run: ContextVar[Any] = ContextVar('comparison_model_run', default=None)
_final_stage: ContextVar[bool] = ContextVar('comparison_final_stage', default=False)


@contextmanager
def final_answer_stage():
    """Mark only the fixed workflow's final summarization model call."""
    token = _final_stage.set(True)
    try:
        yield
    finally:
        _final_stage.reset(token)


class ModelBudget:
    def __init__(
        self, strategy: str, limits: BudgetLimits | None = None,
        quota: SharedQuota | None = None, judge_limits: BudgetLimits | None = None,
        clock: Callable[[], float] = perf_counter,
    ):
        self.strategy = strategy
        self.limits = limits or BudgetLimits()
        self.judge_limits = judge_limits or JUDGE_LIMITS
        self.quota = quota if quota is not None else SharedQuota()
        self.clock = clock
        self.phase = 'generation'
        self.started = clock()
        self.phase_started = {'generation': self.started}
        self.usage = {'generation': _Usage(), 'judge': _Usage()}
        self.violation_code: str | None = None
        self.first_model_event_seconds: float | None = None
        self.final_answer_first_token_seconds: float | None = None
        self.request_trace: list[dict[str, Any]] = []

    def __enter__(self):
        self._token = _current_scope.set(self)
        return self

    def __exit__(self, *_):
        _current_scope.reset(self._token)

    def _fail(self, code: str):
        if self.violation_code is None:
            self.violation_code = code
        raise BudgetExceeded(self.violation_code)

    def _limits(self) -> BudgetLimits:
        return self.limits if self.phase == 'generation' else self.judge_limits

    def remaining_seconds(self) -> float:
        return self._limits().timeout_seconds - (self.clock() - self.phase_started[self.phase])

    def check(self):
        if self.violation_code:
            raise BudgetExceeded(self.violation_code)
        if self.remaining_seconds() <= 0:
            self._fail('timeout')

    def check_work(self):
        """Check before starting more retrieval/model work, never after success.

        check() remains suitable for final status checks when the last permitted
        call exactly exhausted a quota. This method deliberately marks exhaustion
        as a violation only when the caller attempts to start additional work.
        """
        self.check()
        limits, usage, quota = self._limits(), self.usage[self.phase], self.quota
        for exhausted, code in (
            (usage.calls >= limits.max_calls, 'calls'),
            (quota.used_calls >= quota.max_calls, 'shared_calls'),
            (usage.output_tokens >= limits.max_output_tokens, 'output_tokens'),
            (quota.used_output_tokens >= quota.max_output_tokens, 'shared_output_tokens'),
            (usage.context_bytes >= limits.max_context_bytes, 'context_bytes'),
            (quota.used_context_bytes >= quota.max_context_bytes, 'shared_context_bytes'),
        ):
            if exhausted:
                self._fail(code)

    def set_phase(self, phase: str):
        self.check()
        if phase not in ('generation', 'judge'):
            raise ValueError('phase must be generation or judge')
        self.phase = phase
        self.phase_started.setdefault(phase, self.clock())
        self.check()

    def to_dict(self) -> dict[str, Any]:
        return {
            'strategy': self.strategy, 'phase': self.phase,
            'generation': asdict(self.usage['generation']),
            'judge': asdict(self.usage['judge']),
            'limits': asdict(self.limits), 'judge_limits': asdict(self.judge_limits),
            'violation_code': self.violation_code,
            'first_model_event_seconds': self.first_model_event_seconds,
            'final_answer_first_token_seconds': self.final_answer_first_token_seconds,
            'request_trace': self.request_trace,
        }

    def reserve(self, body: dict[str, Any]) -> tuple[int, int]:
        self.check()
        limits, usage, quota = self._limits(), self.usage[self.phase], self.quota
        context = {key: body[key] for key in ('messages', 'tools') if key in body}
        size = len(json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))
        for invalid, code in (
            (usage.calls >= limits.max_calls, 'calls'),
            (quota.used_calls >= quota.max_calls, 'shared_calls'),
            (size > limits.max_request_context_bytes, 'request_context_bytes'),
            (usage.context_bytes + size > limits.max_context_bytes, 'context_bytes'),
            (quota.used_context_bytes + size > quota.max_context_bytes, 'shared_context_bytes'),
            (usage.output_tokens >= limits.max_output_tokens, 'output_tokens'),
            (quota.used_output_tokens >= quota.max_output_tokens, 'shared_output_tokens'),
        ):
            if invalid:
                self._fail(code)
        allocation = min(1024, limits.max_tokens_per_call, limits.max_output_tokens - usage.output_tokens, quota.max_output_tokens - quota.used_output_tokens)
        if allocation <= 0:
            self._fail('output_tokens')
        usage.calls += 1
        usage.context_bytes += size
        usage.reserved_output_tokens += allocation
        usage.output_tokens += allocation
        quota.used_calls += 1
        quota.used_context_bytes += size
        quota.used_output_tokens += allocation
        return allocation, size


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: _safe(item) for key, item in value.items() if key not in (
            'reasoning', 'reasoning_content', 'reasoning_details', 'thinking',
            'headers', 'authorization', 'api_key', 'error',
        )}
        if value.get('role') == 'tool' and isinstance(value.get('content'), str):
            try:
                payload = json.loads(value['content'])
            except (ValueError, TypeError):
                # Non-JSON tool text may be evidence: preserve it verbatim.
                pass
            else:
                result['content'] = json.dumps(_safe(payload), ensure_ascii=False)
        return result
    if isinstance(value, list):
        return [_safe(item) for item in value if not (
            isinstance(item, dict) and item.get('type') in ('reasoning', 'thinking', 'redacted_thinking')
        )]
    return value


class _BudgetCallback(BaseCallbackHandler):
    raise_error = True

    def __init__(self):
        self.runs: dict[Any, dict[str, Any]] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        scope = _current_scope.get()
        if scope is None:
            raise RuntimeError('comparison client requires ModelBudget')
        scope.check()
        first = messages[0][0] if messages and messages[0] else None
        final = scope.strategy == 'rag' or (
            scope.strategy == 'fixed' and _final_stage.get()
        ) or (
            scope.strategy == 'agent' and getattr(first, 'type', None) == 'system'
            and first.content == AGENT_LOOP_SYSTEM_PROMPT
        )
        run = {'scope': scope, 'phase': scope.phase, 'final': final, 'candidate': None,
               'trace': None, 'raw_usage': None, 'run_id': str(run_id)}
        run['context_token'] = _current_run.set(run)
        self.runs[run_id] = run

    def on_llm_new_token(self, token, *, run_id, **kwargs):
        run = self.runs.get(run_id)
        if not run:
            return
        scope = run['scope']
        scope.check()
        if run['phase'] != 'generation':
            return
        now = scope.clock() - scope.started
        if scope.first_model_event_seconds is None:
            scope.first_model_event_seconds = now
        if run['candidate'] is None and token and token.strip():
            run['candidate'] = now

    def _finish(self, run_id):
        run = self.runs.pop(run_id, None)
        if run:
            _current_run.reset(run['context_token'])
        return run

    def on_llm_end(self, response, *, run_id, **kwargs):
        run = self.runs.get(run_id)
        if not run:
            return
        try:
            scope, trace = run['scope'], run['trace']
            scope.check()
            if trace is None:
                return
            generations = response.generations[0]
            message = generations[0].message
            trace['output'] = _safe({'content': message.content, 'tool_calls': message.tool_calls})
            trace['finish_reason'] = message.response_metadata.get('finish_reason')
            metadata = run['raw_usage']
            actual = metadata.get('completion_tokens') if isinstance(metadata, dict) else None
            if isinstance(actual, int) and not isinstance(actual, bool) and actual >= 0:
                trace['usage'] = {'prompt_tokens': metadata.get('prompt_tokens'), 'completion_tokens': actual, 'total_tokens': metadata.get('total_tokens')}
                usage = scope.usage[run['phase']]
                reserved = trace['reserved_output_tokens']
                usage.actual_output_tokens += actual
                usage.output_tokens += actual - reserved
                scope.quota.used_output_tokens += actual - reserved
                if actual > reserved:
                    scope._fail('output_tokens')
            if run['phase'] == 'generation' and run['final'] and not message.tool_calls and run['candidate'] is not None:
                if scope.final_answer_first_token_seconds is None:
                    scope.final_answer_first_token_seconds = run['candidate']
        finally:
            self._finish(run_id)

    def on_llm_error(self, error, *, run_id, **kwargs):
        run = self._finish(run_id)
        if run:
            if run['trace'] is not None:
                run['trace']['error_type'] = type(error).__name__
            run['scope'].check()


class _DeadlineStream(httpx.SyncByteStream):
    def __init__(self, stream, scope, run):
        self.stream, self.scope, self.run = stream, scope, run

    def __iter__(self):
        # Inspect one SSE event at a time without eagerly consuming any chunks.
        # LangChain normalizes a missing completion_tokens to zero, so reliable
        # accounting must inspect the provider's actual field before normalization.
        pending = b''
        for chunk in self.stream:
            self.scope.check()
            pending += chunk
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                if not line.startswith(b'data:'):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except (ValueError, UnicodeDecodeError):
                    continue
                usage = event.get('usage') if isinstance(event, dict) else None
                if isinstance(usage, dict):
                    self.run['raw_usage'] = {
                        key: usage[key] for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                        if key in usage
                    }
            yield chunk
        self.scope.check()

    def close(self):
        self.stream.close()


class _GuardTransport(httpx.BaseTransport):
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def handle_request(self, request):
        scope, run = _current_scope.get(), _current_run.get()
        if scope is None or run is None:
            raise RuntimeError('comparison request requires active model scope/run')
        body = json.loads(request.content)
        allocation, size = scope.reserve(body)
        body.pop('max_completion_tokens', None)
        body['max_tokens'] = allocation
        trace = {'run_id': run['run_id'], 'phase': scope.phase,
                 'body': _safe({key: body[key] for key in ('messages', 'tools', 'model', 'max_tokens') if key in body}),
                 'context_bytes': size, 'reserved_output_tokens': allocation,
                 'http_status': None, 'finish_reason': None, 'usage': None}
        scope.request_trace.append(trace)
        run['trace'] = trace
        timeout = dict(request.extensions.get('timeout', {}))
        remaining = scope.remaining_seconds()
        for kind in ('connect', 'read', 'write', 'pool'):
            old = timeout.get(kind)
            timeout[kind] = min(old, remaining) if old is not None else remaining
        forwarded = httpx.Request(request.method, request.url, headers=request.headers,
                                  content=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                                  extensions={**request.extensions, 'timeout': timeout})
        # Request body length changed: never forward the original Content-Length.
        forwarded.headers['content-length'] = str(len(forwarded.content))
        response = self.wrapped.handle_request(forwarded)
        trace['http_status'] = response.status_code
        try:
            scope.check()
        except BudgetExceeded:
            response.close()
            raise
        response.stream = _DeadlineStream(response.stream, scope, run)
        return response

    def close(self):
        self.wrapped.close()


@contextmanager
def comparison_clients(transport_factory=None):
    """Temporarily route all shared get_client globals through guarded sync HTTP.

    transport_factory is a zero-argument factory returning an httpx.BaseTransport.
    Never use this process-global patch concurrently with application requests.
    """
    original = llm_client.ChatOpenAI
    config = llm_client.config
    retry_names = ('LLM_MAX_RETRIES', 'MAX_GENERATION_RETRY', 'MAX_RETRIEVAL_RETRY')
    saved = {name: getattr(config, name) for name in retry_names}
    clients = []

    def guarded_client(*args, **kwargs):
        transport = transport_factory() if transport_factory else httpx.HTTPTransport(retries=0, trust_env=False)
        client = httpx.Client(transport=_GuardTransport(transport), trust_env=False)
        clients.append(client)
        kwargs.update(http_client=client, streaming=True, stream_usage=True, max_retries=0,
                      http_socket_options=())
        kwargs['callbacks'] = [*(kwargs.get('callbacks') or []), _BudgetCallback()]
        return original(*args, **kwargs)

    try:
        for name in retry_names:
            setattr(config, name, 0)
        llm_client.ChatOpenAI = guarded_client
        yield
    finally:
        llm_client.ChatOpenAI = original
        for name, value in saved.items():
            setattr(config, name, value)
        for client in clients:
            client.close()
