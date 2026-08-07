from rag_app.scripts.benchmark_answer_cache import summarize_runs


def test_summarize_runs_separates_hit_and_miss_latency() -> None:
    summary = summarize_runs(
        [
            {
                "cache_status": "miss",
                "latency_ms": 12_000.0,
                "response_sha256": "same",
            },
            {
                "cache_status": "hit",
                "latency_ms": 8.0,
                "response_sha256": "same",
            },
            {
                "cache_status": "hit",
                "latency_ms": 10.0,
                "response_sha256": "same",
            },
        ]
    )

    assert summary == {
        "requests": 3,
        "hits": 2,
        "misses": 1,
        "hit_rate": 2 / 3,
        "hit_latency_p50_ms": 9.0,
        "miss_latency_p50_ms": 12_000.0,
        "responses_consistent": True,
    }
