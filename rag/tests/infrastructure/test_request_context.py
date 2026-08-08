import logging

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from rag_app.infrastructure.request_context import (
    REQUEST_ID_HEADER,
    RequestIdFilter,
    RequestIdMiddleware,
    get_request_id,
    new_request_id,
    sanitize_request_id,
)


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/sync")
    def sync_endpoint() -> dict[str, str]:
        # 同步路由跑在 FastAPI 线程池里，这里能不能读到 id 是关键。
        return {"request_id": get_request_id()}

    @app.get("/async")
    async def async_endpoint() -> dict[str, str]:
        return {"request_id": get_request_id()}

    @app.get("/stream")
    def stream_endpoint() -> StreamingResponse:
        def generate():
            yield get_request_id()

        return StreamingResponse(generate(), media_type="text/plain")

    return app


client = TestClient(build_app())


def test_generates_an_id_and_returns_it_in_the_response_header() -> None:
    response = client.get("/async")

    request_id = response.json()["request_id"]
    assert request_id != "-"
    assert response.headers[REQUEST_ID_HEADER] == request_id


def test_reuses_a_valid_incoming_id() -> None:
    response = client.get("/async", headers={REQUEST_ID_HEADER: "abc-123_X"})

    assert response.json()["request_id"] == "abc-123_X"
    assert response.headers[REQUEST_ID_HEADER] == "abc-123_X"


def test_rejects_an_incoming_id_that_could_forge_log_lines() -> None:
    response = client.get(
        "/async",
        headers={REQUEST_ID_HEADER: "abc INFO forged log line"},
    )

    request_id = response.json()["request_id"]
    assert " " not in request_id
    assert request_id != "abc INFO forged log line"


def test_id_reaches_sync_routes_running_in_the_threadpool() -> None:
    response = client.get("/sync", headers={REQUEST_ID_HEADER: "threadpool1"})

    assert response.json()["request_id"] == "threadpool1"


def test_id_reaches_sync_streaming_generators() -> None:
    response = client.get("/stream", headers={REQUEST_ID_HEADER: "streamed1"})

    assert response.text == "streamed1"


def test_requests_do_not_leak_ids_into_each_other() -> None:
    first = client.get("/async", headers={REQUEST_ID_HEADER: "first1"})
    second = client.get("/async")

    assert first.json()["request_id"] == "first1"
    assert second.json()["request_id"] != "first1"


def test_sanitize_request_id_rules() -> None:
    assert sanitize_request_id("ok-id_1.2") == "ok-id_1.2"
    assert sanitize_request_id("  padded  ") == "padded"
    assert sanitize_request_id("bad id") is None
    assert sanitize_request_id("bad\nid") is None
    assert sanitize_request_id("") is None
    assert sanitize_request_id(None) is None
    assert sanitize_request_id("a" * 200) == "a" * 64


def test_new_request_id_is_short_and_unique() -> None:
    first = new_request_id()
    second = new_request_id()

    assert first != second
    assert len(first) == 12


def test_filter_attaches_the_current_id_to_records() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    assert RequestIdFilter().filter(record) is True
    assert record.request_id == "-"
