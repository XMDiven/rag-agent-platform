import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import httpx


def find_cache_status(trace: list[dict[str, Any]]) -> str:
    for item in trace:
        if item.get("step") == "answer_cache":
            return str(item.get("status", "unknown"))
    return "disabled"


def fingerprint_response(payload: dict[str, Any]) -> str:
    canonical_response = json.dumps(
        {
            "answer": payload.get("answer"),
            "sources": payload.get("sources", []),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_response.encode("utf-8")).hexdigest()


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, int | float | None]:
    hit_latencies = [
        float(run["latency_ms"])
        for run in runs
        if run["cache_status"] == "hit"
    ]
    miss_latencies = [
        float(run["latency_ms"])
        for run in runs
        if run["cache_status"] == "miss"
    ]
    request_count = len(runs)
    hit_count = len(hit_latencies)
    response_hashes = {
        str(run["response_sha256"])
        for run in runs
        if "response_sha256" in run
    }

    return {
        "requests": request_count,
        "hits": hit_count,
        "misses": len(miss_latencies),
        "hit_rate": hit_count / request_count if request_count else 0.0,
        "hit_latency_p50_ms": median(hit_latencies) if hit_latencies else None,
        "miss_latency_p50_ms": median(miss_latencies) if miss_latencies else None,
        "responses_consistent": bool(runs) and len(response_hashes) == 1,
    }


def run_benchmark(
    base_url: str,
    question: str,
    repeat: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    with httpx.Client(base_url=base_url, timeout=timeout_seconds) as client:
        for run_number in range(1, repeat + 1):
            started_at = perf_counter()
            response = client.post("/ask", json={"question": question})
            latency_ms = round((perf_counter() - started_at) * 1000, 2)
            response.raise_for_status()
            payload = response.json()
            runs.append(
                {
                    "run": run_number,
                    "cache_status": find_cache_status(payload.get("trace", [])),
                    "latency_ms": latency_ms,
                    "source_count": len(payload.get("sources", [])),
                    "response_sha256": fingerprint_response(payload),
                }
            )

    return {
        "base_url": base_url,
        "question": question,
        "runs": runs,
        "summary": summarize_runs(runs),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure Redis exact-answer cache hit latency.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument(
        "--question",
        default="LangChain 和 LlamaIndex 分别适合做什么？",
    )
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_benchmark(
        base_url=args.base_url,
        question=args.question,
        repeat=args.repeat,
        timeout_seconds=args.timeout,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
