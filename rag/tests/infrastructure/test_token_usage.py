import logging
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_app.infrastructure import token_usage
from rag_app.infrastructure.request_context import RequestIdMiddleware
from rag_app.infrastructure.token_usage import (
    RequestUsage,
    TokenUsageCallback,
    extract_usage,
    get_request_usage,
    log_request_usage,
)


def llm_response(
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
):
    message = SimpleNamespace(
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": cache_read},
        }
    )
    return SimpleNamespace(generations=[[SimpleNamespace(message=message)]])


def test_cached_input_is_counted_once_and_billed_separately(monkeypatch) -> None:
    monkeypatch.setattr(token_usage.config, "LLM_INPUT_PRICE_PER_MILLION", 10.0)
    monkeypatch.setattr(
        token_usage.config,
        "LLM_CACHED_INPUT_PRICE_PER_MILLION",
        1.0,
    )
    monkeypatch.setattr(token_usage.config, "LLM_OUTPUT_PRICE_PER_MILLION", 30.0)

    usage = RequestUsage()
    usage.add(input_tokens=1000, cached_input_tokens=800, output_tokens=500)

    assert usage.input_tokens == 1000
    assert usage.billable_input_tokens == 200
    # 200*10 + 800*1 + 500*30 = 2000 + 800 + 15000 = 17800 per million
    assert usage.cost() == round(17800 / 1_000_000, 6)


def test_cost_is_zero_but_tokens_still_counted_without_prices(monkeypatch) -> None:
    monkeypatch.setattr(token_usage.config, "LLM_INPUT_PRICE_PER_MILLION", 0.0)
    monkeypatch.setattr(
        token_usage.config,
        "LLM_CACHED_INPUT_PRICE_PER_MILLION",
        0.0,
    )
    monkeypatch.setattr(token_usage.config, "LLM_OUTPUT_PRICE_PER_MILLION", 0.0)

    usage = RequestUsage()
    usage.add(input_tokens=1000, cached_input_tokens=0, output_tokens=500)

    assert usage.cost() == 0.0
    assert usage.input_tokens == 1000
    assert usage.output_tokens == 500


def test_callback_accumulates_every_llm_call() -> None:
    token_usage.start_request_usage()
    callback = TokenUsageCallback()

    callback.on_llm_end(llm_response(100, 20, cache_read=40))
    callback.on_llm_end(llm_response(300, 60))

    usage = get_request_usage()
    assert usage is not None
    assert usage.llm_calls == 2
    assert usage.input_tokens == 400
    assert usage.cached_input_tokens == 40
    assert usage.output_tokens == 80


def test_callback_is_a_noop_outside_a_request() -> None:
    token_usage._request_usage.set(None)

    TokenUsageCallback().on_llm_end(llm_response(100, 20))

    assert get_request_usage() is None


def test_extract_usage_ignores_responses_without_metadata() -> None:
    assert extract_usage(SimpleNamespace()) is None
    assert extract_usage(SimpleNamespace(usage_metadata=None)) is None
    assert extract_usage(
        SimpleNamespace(usage_metadata={"input_tokens": 5, "output_tokens": 2})
    ) == (5, 0, 2)


def test_usage_recorded_in_the_threadpool_reaches_the_middleware(caplog) -> None:
    """同步路由跑在线程池里，子线程的 set() 中间件看不到，只能共享同一个对象。"""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/sync")
    def sync_endpoint() -> dict[str, str]:
        TokenUsageCallback().on_llm_end(llm_response(700, 300, cache_read=200))
        return {"ok": "yes"}

    with caplog.at_level(logging.INFO, logger=token_usage.logger.name):
        TestClient(app).get("/sync")

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "usage.request llm_calls=1 input=700 cached_input=200 output=300" in message
        for message in messages
    ), messages


def test_no_usage_line_when_no_llm_was_called(caplog) -> None:
    with caplog.at_level(logging.INFO, logger=token_usage.logger.name):
        log_request_usage(RequestUsage())

    assert caplog.records == []
