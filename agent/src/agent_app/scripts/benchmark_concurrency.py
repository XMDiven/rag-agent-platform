"""HTTP 并发压测：按并发梯度测 P50/P95、吞吐和错误率。

与 `rag_app.scripts.benchmark_latency` 的区别：那个在进程内串行调用
`ask_question()`，量的是单次链路耗时；这个走真实 HTTP，量的是并发下的
排队行为——同步路由跑在 FastAPI 线程池里，并发上限和排队从未实测过。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from agent_app.scripts.evaluate_agent import (
    AGENT_PROJECT_ROOT,
    nearest_rank_percentile,
)

DEFAULT_URL = "http://127.0.0.1:8002/agent/run"
DEFAULT_QUESTION = "什么是检索增强生成（RAG）？"
DEFAULT_OUTPUT_DIR = AGENT_PROJECT_ROOT / "experiments" / "runs" / "concurrency"

RequestFn = Callable[[], Awaitable[dict[str, Any]]]


async def timed_request(
    client: httpx.AsyncClient,
    url: str,
    question: str,
) -> dict[str, Any]:
    started_at = perf_counter()
    try:
        response = await client.post(url, json={"question": question})
    except Exception as error:
        return {
            "ok": False,
            "status_code": None,
            "error_type": type(error).__name__,
            "duration_seconds": round(perf_counter() - started_at, 3),
        }

    return {
        "ok": response.status_code == 200,
        "status_code": response.status_code,
        "error_type": None,
        "duration_seconds": round(perf_counter() - started_at, 3),
    }


async def run_level(
    request_fn: RequestFn,
    concurrency: int,
    total_requests: int,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded() -> dict[str, Any]:
        async with semaphore:
            return await request_fn()

    started_at = perf_counter()
    results = await asyncio.gather(
        *(guarded() for _ in range(total_requests))
    )
    wall_seconds = perf_counter() - started_at

    return summarize_level(
        concurrency=concurrency,
        results=list(results),
        wall_seconds=wall_seconds,
    )


def summarize_level(
    concurrency: int,
    results: list[dict[str, Any]],
    wall_seconds: float,
) -> dict[str, Any]:
    successful = [result for result in results if result["ok"]]
    durations = [float(result["duration_seconds"]) for result in successful]

    if durations:
        p50 = round(nearest_rank_percentile(durations, 0.50), 3)
        p95 = round(nearest_rank_percentile(durations, 0.95), 3)
        average = round(sum(durations) / len(durations), 3)
        slowest = round(max(durations), 3)
    else:
        p50 = p95 = average = slowest = 0.0

    error_types: dict[str, int] = {}
    for result in results:
        if result["ok"]:
            continue
        key = str(result["error_type"] or result["status_code"])
        error_types[key] = error_types.get(key, 0) + 1

    return {
        "concurrency": concurrency,
        "requests": len(results),
        "succeeded": len(successful),
        "failed": len(results) - len(successful),
        "error_rate": round(
            (len(results) - len(successful)) / len(results), 3
        )
        if results
        else 0.0,
        "wall_seconds": round(wall_seconds, 3),
        "throughput_rps": round(len(successful) / wall_seconds, 3)
        if wall_seconds
        else 0.0,
        "average_seconds": average,
        "p50_seconds": p50,
        "p95_seconds": p95,
        "max_seconds": slowest,
        "error_types": error_types,
    }


async def run_benchmark(
    url: str,
    question: str,
    levels: list[int],
    requests_per_level: int,
    timeout_seconds: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for concurrency in levels:
            print(
                f"concurrency={concurrency} requests={requests_per_level}",
                flush=True,
            )
            level = await run_level(
                request_fn=lambda: timed_request(client, url, question),
                concurrency=concurrency,
                total_requests=requests_per_level,
            )
            results.append(level)
            print(
                (
                    f"  p50={level['p50_seconds']}s "
                    f"p95={level['p95_seconds']}s "
                    f"rps={level['throughput_rps']} "
                    f"errors={level['failed']}"
                ),
                flush=True,
            )

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "run_id": timestamp.strftime("%Y%m%d-%H%M%S"),
        "url": url,
        "requests_per_level": requests_per_level,
        "timeout_seconds": timeout_seconds,
        "levels": results,
    }


def write_report(
    report: dict[str, Any],
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{report['run_id']}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def parse_levels(raw: str) -> list[int]:
    levels = [int(item) for item in raw.split(",") if item.strip()]
    if not levels or any(level < 1 for level in levels):
        raise ValueError("--levels must be positive integers")

    return levels


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure HTTP latency and throughput under concurrency.",
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--levels", default="1,2,4")
    parser.add_argument("--requests-per-level", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = asyncio.run(
        run_benchmark(
            url=args.url,
            question=args.question,
            levels=parse_levels(args.levels),
            requests_per_level=args.requests_per_level,
            timeout_seconds=args.timeout_seconds,
        )
    )
    output_path = write_report(report, output_dir=args.output_dir)

    print(f"Concurrency report: {output_path}")
    for level in report["levels"]:
        print(
            (
                f"concurrency={level['concurrency']} "
                f"p50={level['p50_seconds']}s p95={level['p95_seconds']}s "
                f"rps={level['throughput_rps']} "
                f"error_rate={level['error_rate']}"
            )
        )
    return 0 if all(level["failed"] == 0 for level in report["levels"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
