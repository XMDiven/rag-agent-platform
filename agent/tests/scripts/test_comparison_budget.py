import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from agent_app.prompts import AGENT_LOOP_SYSTEM_PROMPT
from agent_app.scripts import comparison_budget as budget
from rag_app.infrastructure import llm_client


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class SSE(httpx.SyncByteStream):
    def __init__(self, chunks, clock):
        self.chunks = chunks
        self.clock = clock

    def __iter__(self):
        for chunk in self.chunks:
            self.clock.value += 1
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


def response(clock, *, text='answer', tokens=3, tools=False):
    delta = {'content': text}
    if tools:
        delta['tool_calls'] = [{'index': 0, 'id': 'call_a', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}}]
    chunks = [
        {'id': 'x', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]},
        {'id': 'x', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
        {'id': 'x', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if tools else 'stop'}]},
    ]
    if tokens is not None:
        chunks.append({'id': 'x', 'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': tokens, 'total_tokens': 10 + tokens}})
    return httpx.Response(200, stream=SSE(chunks, clock))


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(llm_client.config, 'settings', SimpleNamespace(
        llm_api_key='secret-test-key', llm_base_url='https://unit.invalid/v1',
        llm_model_id='unit-model', llm_thinking_type=None,
    ))


def test_defaults_and_quota_roundtrip():
    limits = budget.BudgetLimits()
    assert (limits.max_calls, limits.max_output_tokens, limits.max_tokens_per_call) == (6, 4096, 1024)
    assert (limits.max_context_bytes, limits.max_request_context_bytes, limits.timeout_seconds) == (120000, 32000, 180)
    quota = budget.SharedQuota(used_calls=2, used_output_tokens=9, used_context_bytes=11)
    assert budget.SharedQuota(**quota.to_dict()).to_dict() == quota.to_dict()


def test_real_stream_caps_request_and_settles_usage():
    clock = Clock()
    sent = []
    def send(request):
        sent.append(json.loads(request.content))
        assert request.extensions['timeout']['read'] <= 180
        return response(clock)
    quota = budget.SharedQuota(max_output_tokens=7)
    with budget.comparison_clients(lambda: httpx.MockTransport(send)):
        with budget.ModelBudget('rag', quota=quota, clock=clock) as scope:
            result = llm_client.get_client().invoke([HumanMessage(content='你好')])
    assert result.content == 'answer'
    assert sent[0]['max_tokens'] == 7
    assert 'max_completion_tokens' not in sent[0]
    stats = scope.to_dict()
    assert stats['generation']['reserved_output_tokens'] == 7
    assert stats['generation']['actual_output_tokens'] == 3
    assert quota.used_output_tokens == 3
    assert stats['first_model_event_seconds'] == 1
    assert stats['final_answer_first_token_seconds'] == 2
    assert stats['request_trace'][0]['usage']['completion_tokens'] == 3


@pytest.mark.parametrize('limits,quota,code', [
    ({'max_calls': 0}, {}, 'calls'),
    ({'max_request_context_bytes': 1}, {}, 'request_context_bytes'),
    ({'max_context_bytes': 1}, {}, 'context_bytes'),
    ({'max_output_tokens': 0}, {}, 'output_tokens'),
    ({}, {'max_calls': 0}, 'shared_calls'),
    ({}, {'max_context_bytes': 0}, 'shared_context_bytes'),
    ({}, {'max_output_tokens': 0}, 'shared_output_tokens'),
    ({'timeout_seconds': 0}, {}, 'timeout'),
])
def test_blocked_request_never_forwarded(limits, quota, code):
    sent = []
    with budget.comparison_clients(lambda: httpx.MockTransport(lambda request: sent.append(request))):
        with budget.ModelBudget('rag', limits=budget.BudgetLimits(**limits), quota=budget.SharedQuota(**quota)) as scope:
            with pytest.raises(Exception):
                llm_client.get_client().invoke('hello')
            assert scope.violation_code == code
            with pytest.raises(budget.BudgetExceeded):
                scope.check()
    assert sent == []


def test_generation_judge_independent_and_shared():
    clock = Clock()
    sent = []
    def send(request):
        sent.append(json.loads(request.content))
        return response(clock, tokens=2)
    quota = budget.SharedQuota(max_output_tokens=9)
    with budget.comparison_clients(lambda: httpx.MockTransport(send)):
        with budget.ModelBudget('rag', limits=budget.BudgetLimits(max_calls=1), quota=quota, clock=clock) as scope:
            llm_client.get_client().invoke('generate')
            first = scope.to_dict()['first_model_event_seconds']
            scope.set_phase('judge')
            llm_client.get_client().invoke('judge')
            with pytest.raises(Exception):
                llm_client.get_client().invoke('judge again')
    assert [item['max_tokens'] for item in sent] == [9, 7]
    assert scope.to_dict()['generation']['calls'] == scope.to_dict()['judge']['calls'] == 1
    assert scope.to_dict()['first_model_event_seconds'] == first
    assert quota.used_calls == 2
    assert quota.used_output_tokens == 4


def test_missing_usage_preserves_reservation_and_blocks_next():
    clock = Clock()
    sent = []
    def send(request):
        sent.append(request)
        return response(clock, tokens=None)
    with budget.comparison_clients(lambda: httpx.MockTransport(send)):
        with budget.ModelBudget('rag', limits=budget.BudgetLimits(max_output_tokens=5), clock=clock) as scope:
            llm_client.get_client().invoke('first')
            with pytest.raises(Exception):
                llm_client.get_client().invoke('second')
    assert len(sent) == 1
    assert scope.to_dict()['generation']['output_tokens'] == 5


def test_fixed_final_stage_and_agent_tool_planning():
    for strategy in ('fixed', 'agent'):
        clock = Clock()
        count = 0
        def send(request):
            nonlocal count
            count += 1
            return response(clock, tools=(strategy == 'agent' and count == 1))
        main = [SystemMessage(content=AGENT_LOOP_SYSTEM_PROMPT), HumanMessage(content='question')]
        with budget.comparison_clients(lambda: httpx.MockTransport(send)):
            with budget.ModelBudget(strategy, clock=clock) as scope:
                llm_client.get_client().invoke(main if strategy == 'agent' else 'subquestion')
                llm_client.get_client().invoke('internal rag')
                assert scope.to_dict()['final_answer_first_token_seconds'] is None
                with budget.final_answer_stage():
                    llm_client.get_client().invoke(main if strategy == 'agent' else 'summary')
        assert scope.to_dict()['first_model_event_seconds'] == 1
        assert scope.to_dict()['final_answer_first_token_seconds'] == 10


def test_late_stream_timeout_is_sticky_and_keeps_reservation():
    clock = Clock()
    with budget.comparison_clients(lambda: httpx.MockTransport(lambda request: response(clock))):
        with budget.ModelBudget('rag', limits=budget.BudgetLimits(timeout_seconds=2), clock=clock) as scope:
            with pytest.raises(Exception):
                llm_client.get_client().invoke('hello')
            with pytest.raises(budget.BudgetExceeded):
                scope.check()
    assert scope.violation_code == 'timeout'
    assert scope.to_dict()['generation']['output_tokens'] == 1024


def test_patch_restores_and_error_trace_excludes_secrets():
    original = llm_client.ChatOpenAI
    retries = llm_client.config.LLM_MAX_RETRIES
    def send(request):
        return httpx.Response(500, json={'error': {'message': 'private upstream detail secret-test-key'}}, headers={'x-secret': 'hidden'})
    with budget.comparison_clients(lambda: httpx.MockTransport(send)):
        assert llm_client.config.LLM_MAX_RETRIES == 0
        with budget.ModelBudget('rag') as scope:
            with pytest.raises(Exception):
                llm_client.get_client().invoke('safe question')
    assert llm_client.ChatOpenAI is original
    assert llm_client.config.LLM_MAX_RETRIES == retries
    trace = json.dumps(scope.to_dict()['request_trace'])
    assert 'private upstream' not in trace
    assert 'secret-test-key' not in trace
    assert 'hidden' not in trace
    assert scope.to_dict()['generation']['output_tokens'] == 1024


def test_partial_usage_is_not_reliable_and_keeps_reservation():
    clock = Clock()
    def send(request):
        result = response(clock, tokens=None)
        result.stream.chunks.append({'id': 'x', 'choices': [], 'usage': {'prompt_tokens': 10}})
        return result
    with budget.comparison_clients(lambda: httpx.MockTransport(send)):
        with budget.ModelBudget('rag', clock=clock) as scope:
            llm_client.get_client().invoke('question')
    assert scope.to_dict()['generation']['output_tokens'] == 1024
    assert scope.to_dict()['request_trace'][0]['usage'] is None


def test_context_bytes_count_utf8_canonical_messages_and_tools_cumulatively():
    clock = Clock()
    sent = []
    def send(request):
        sent.append(json.loads(request.content))
        return response(clock, tokens=1)
    with budget.comparison_clients(lambda: httpx.MockTransport(send)):
        with budget.ModelBudget('rag', clock=clock) as scope:
            llm_client.get_client().bind_tools([{'type': 'function', 'function': {'name': 'lookup', 'description': '中文', 'parameters': {'type': 'object', 'properties': {}}}}]).invoke('你好')
            expected = len(json.dumps({key: sent[0][key] for key in ('messages', 'tools')}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))
            assert scope.to_dict()['generation']['context_bytes'] == expected
            scope.limits = budget.BudgetLimits(max_context_bytes=expected)
            with pytest.raises(Exception):
                llm_client.get_client().invoke('another')
    assert len(sent) == 1
    assert scope.violation_code == 'context_bytes'


def test_usage_over_provider_cap_is_sticky():
    clock = Clock()
    with budget.comparison_clients(lambda: httpx.MockTransport(lambda request: response(clock, tokens=9))):
        with budget.ModelBudget('rag', limits=budget.BudgetLimits(max_output_tokens=4), clock=clock) as scope:
            with pytest.raises(budget.BudgetExceeded):
                llm_client.get_client().invoke('question')
            with pytest.raises(budget.BudgetExceeded):
                scope.check()
    assert scope.to_dict()['generation']['actual_output_tokens'] == 9
    assert scope.violation_code == 'output_tokens'


def test_failed_stream_with_usage_keeps_reservation():
    clock = Clock()
    class Broken(SSE):
        def __iter__(self):
            for chunk in super().__iter__():
                if chunk != b'data: [DONE]\n\n':
                    yield chunk
            raise httpx.ReadError('private-network-error')
    def send(request):
        result = response(clock, tokens=2)
        result.stream = Broken(result.stream.chunks, clock)
        return result
    with budget.comparison_clients(lambda: httpx.MockTransport(send)):
        with budget.ModelBudget('rag', clock=clock) as scope:
            with pytest.raises(httpx.ReadError):
                llm_client.get_client().invoke('question')
    assert scope.to_dict()['generation']['output_tokens'] == 1024
    assert 'private-network-error' not in json.dumps(scope.to_dict())


def test_judge_deadline_starts_on_phase_change():
    clock = Clock()
    with budget.comparison_clients(lambda: httpx.MockTransport(lambda request: response(clock))):
        with budget.ModelBudget('rag', clock=clock) as scope:
            clock.value = 170
            scope.set_phase('judge')
            llm_client.get_client().invoke('judge')
    assert scope.to_dict()['first_model_event_seconds'] is None
    assert scope.to_dict()['final_answer_first_token_seconds'] is None


def test_no_retry_and_constructor_options_restored(monkeypatch):
    original = llm_client.ChatOpenAI
    options = []
    def capture(*args, **kwargs):
        options.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(llm_client, 'ChatOpenAI', capture)
    with budget.comparison_clients(lambda: httpx.MockTransport(lambda request: httpx.Response(429))):
        model = llm_client.get_client()
        assert options[0]['http_socket_options'] == ()
        assert len(options[0]['callbacks']) == 2
        assert options[0]['max_retries'] == 0
        assert options[0]['streaming'] is options[0]['stream_usage'] is True
        assert model.root_client.max_retries == 0
    assert llm_client.ChatOpenAI is capture


def test_check_work_blocks_exhausted_quota_without_rejecting_last_success():
    clock = Clock()
    quota = budget.SharedQuota(max_calls=1)
    with budget.comparison_clients(lambda: httpx.MockTransport(lambda request: response(clock))):
        with budget.ModelBudget('rag', quota=quota, clock=clock) as scope:
            llm_client.get_client().invoke('last permitted request')
            scope.check()
            with pytest.raises(budget.BudgetExceeded, match='shared_calls'):
                scope.check_work()


def test_tool_json_trace_scrubs_errors_and_reasoning_but_forwarded_body_is_original():
    clock = Clock()
    sent = []
    tool = {'ok': False, 'error': 'private original exception', 'error_type': 'LookupError', 'reasoning_content': 'hidden reasoning text', 'hits': [{'text': 'verbatim evidence'}]}
    messages = [{'role': 'tool', 'tool_call_id': 'call_a', 'content': json.dumps(tool)}]
    def send(request):
        sent.append(json.loads(request.content))
        return response(clock)
    with budget.comparison_clients(lambda: httpx.MockTransport(send)):
        with budget.ModelBudget('rag', clock=clock) as scope:
            llm_client.get_client().invoke(messages)
    assert json.loads(sent[0]['messages'][0]['content']) == tool
    trace = json.dumps(scope.to_dict()['request_trace'])
    assert 'private original exception' not in trace
    assert 'hidden reasoning text' not in trace
    assert 'LookupError' in trace
    assert 'verbatim evidence' in trace


def test_late_http_response_is_closed_and_reported_as_timeout():
    clock = Clock()
    closed = []
    class TrackClose(SSE):
        def close(self):
            closed.append(True)
    def send(request):
        result = response(clock)
        result.stream = TrackClose(result.stream.chunks, clock)
        clock.value = 999
        return result
    with budget.comparison_clients(lambda: httpx.MockTransport(send)):
        with budget.ModelBudget('rag', clock=clock) as scope:
            with pytest.raises(Exception):
                llm_client.get_client().invoke('question')
    assert scope.violation_code == 'timeout'
    assert closed == [True]


def test_default_transport_disables_environment_and_retries(monkeypatch):
    options = []
    def make_transport(**kwargs):
        options.append(kwargs)
        return httpx.MockTransport(lambda request: httpx.Response(500))
    monkeypatch.setattr(httpx, 'HTTPTransport', make_transport)
    with budget.comparison_clients():
        llm_client.get_client()
    assert options == [{'retries': 0, 'trust_env': False}]
