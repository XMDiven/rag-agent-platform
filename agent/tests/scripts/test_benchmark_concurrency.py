import asyncio
import json

import pytest

from agent_app.scripts.benchmark_concurrency import (
    parse_levels,
    run_benchmark,
    run_level,
    summarize_level,
    write_report,
)


def test_summarize_level_ignores_failed_requests_in_latency() -> None:
    level = summarize_level(
        concurrency=2,
        results=[
            {"ok": True, "status_code": 200, "error_type": None, "duration_seconds": 1.0},
            {"ok": True, "status_code": 200, "error_type": None, "duration_seconds": 3.0},
            {"ok": False, "status_code": 500, "error_type": None, "duration_seconds": 0.1},
            {"ok": False, "status_code": None, "error_type": "ReadTimeout", "duration_seconds": 30.0},
        ],
        wall_seconds=4.0,
    )

    assert level["requests"] == 4
    assert level["succeeded"] == 2
    assert level["failed"] == 2
    assert level["error_rate"] == 0.5
    assert level["average_seconds"] == 2.0
    assert level["max_seconds"] == 3.0
    assert level["throughput_rps"] == 0.5
    assert level["error_types"] == {"500": 1, "ReadTimeout": 1}


def test_summarize_level_handles_all_requests_failing() -> None:
    level = summarize_level(
        concurrency=1,
        results=[
            {"ok": False, "status_code": 502, "error_type": None, "duration_seconds": 0.2},
        ],
        wall_seconds=0.2,
    )

    assert level["succeeded"] == 0
    assert level["p50_seconds"] == 0.0
    assert level["p95_seconds"] == 0.0
    assert level["throughput_rps"] == 0.0
    assert level["error_rate"] == 1.0


def test_run_level_never_exceeds_the_configured_concurrency() -> None:
    in_flight = 0
    peak = 0

    async def request_fn() -> dict[str, object]:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {
            "ok": True,
            "status_code": 200,
            "error_type": None,
            "duration_seconds": 0.01,
        }

    level = asyncio.run(
        run_level(request_fn=request_fn, concurrency=2, total_requests=6)
    )

    assert peak == 2
    assert level["requests"] == 6
    assert level["succeeded"] == 6


def test_parse_levels_rejects_non_positive_values() -> None:
    assert parse_levels("1,2,8") == [1, 2, 8]

    with pytest.raises(ValueError, match="positive integers"):
        parse_levels("0,2")


def test_write_report_round_trips_json(tmp_path) -> None:
    report = {"run_id": "20260804-120000", "url": "http://x", "levels": []}

    output_path = write_report(report, output_dir=tmp_path / "runs")

    assert output_path.name == "20260804-120000.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == report


def test_run_benchmark_reports_every_level(monkeypatch) -> None:
    async def fake_timed_request(client, url, question):
        return {
            "ok": True,
            "status_code": 200,
            "error_type": None,
            "duration_seconds": 0.5,
        }

    monkeypatch.setattr(
        "agent_app.scripts.benchmark_concurrency.timed_request",
        fake_timed_request,
    )

    report = asyncio.run(
        run_benchmark(
            url="http://127.0.0.1:8002/agent/run",
            question="What is RAG?",
            levels=[1, 2],
            requests_per_level=2,
            timeout_seconds=5.0,
        )
    )

    assert [level["concurrency"] for level in report["levels"]] == [1, 2]
    assert all(level["succeeded"] == 2 for level in report["levels"])
    assert report["requests_per_level"] == 2
