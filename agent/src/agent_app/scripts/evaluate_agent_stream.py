from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from agent_app.schemas.stream import AgentStreamEvent
from agent_app.scripts.evaluate_agent import (
    AGENT_PROJECT_ROOT,
    DEFAULT_CASES_PATH,
    AgentEvalCase,
    load_cases,
    nearest_rank_percentile,
    utc_timestamp,
)
from agent_app.streaming_service import stream_agent_events

DEFAULT_OUTPUT_DIR = AGENT_PROJECT_ROOT / "experiments" / "runs" / "streaming"


def evaluate_stream_case(
    case: AgentEvalCase,
    stream_fn: Callable[[str], Iterator[AgentStreamEvent]] = (
        stream_agent_events
    ),
    timer: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    started_at = timer()
    first_event_latency: float | None = None
    first_answer_latency: float | None = None
    total_latency = 0.0
    event_types: list[str] = []
    protocol_errors: list[str] = []
    tools: list[str] = []
    step_count = 0
    source_count = 0
    answer_character_count = 0
    done_count = 0
    phase = "steps"
    error_code: str | None = None
    termination_reason: str | None = None
    selected_tool: str | None = None
    tool_status: str | None = None
    exception_type: str | None = None

    def add_protocol_error(code: str) -> None:
        if code not in protocol_errors:
            protocol_errors.append(code)

    try:
        for event in stream_fn(case.question):
            elapsed = round(timer() - started_at, 3)
            total_latency = elapsed
            if first_event_latency is None:
                first_event_latency = elapsed

            event_types.append(event.type)

            if done_count:
                add_protocol_error("event_after_done")

            if event.type == "step":
                if phase != "steps":
                    add_protocol_error("step_out_of_order")
                step_count += 1
                tools.append(event.data.tool_name)
            elif event.type == "answer_delta":
                if phase in {"sources", "error", "done"}:
                    add_protocol_error("answer_out_of_order")
                phase = "answer"
                answer_character_count += len(event.data.text)
                if first_answer_latency is None:
                    first_answer_latency = elapsed
            elif event.type == "sources":
                if phase != "answer":
                    add_protocol_error("sources_out_of_order")
                phase = "sources"
                source_count = len(event.data.sources)
            elif event.type == "error":
                if phase in {"sources", "error", "done"}:
                    add_protocol_error("error_out_of_order")
                phase = "error"
                error_code = event.data.code
            elif event.type == "done":
                done_count += 1
                if phase not in {"sources", "error"}:
                    add_protocol_error("done_before_result")
                phase = "done"
                termination_reason = event.data.termination_reason
                selected_tool = event.data.selected_tool
                tool_status = event.data.tool_status
    except Exception as error:
        total_latency = round(timer() - started_at, 3)
        exception_type = type(error).__name__
        add_protocol_error("stream_exception")

    if done_count == 0:
        add_protocol_error("missing_done")
    elif done_count > 1:
        add_protocol_error("multiple_done")

    if done_count and (not event_types or event_types[-1] != "done"):
        add_protocol_error("done_not_final")

    completed = (
        done_count == 1
        and bool(event_types)
        and event_types[-1] == "done"
    )
    protocol_valid = not protocol_errors
    stream_succeeded = (
        protocol_valid
        and completed
        and error_code is None
        and tool_status == "success"
    )

    return {
        "id": case.id,
        "question": case.question,
        "event_types": event_types,
        "tools": tools,
        "step_count": step_count,
        "source_count": source_count,
        "answer_character_count": answer_character_count,
        "first_event_latency_seconds": first_event_latency,
        "first_answer_latency_seconds": first_answer_latency,
        "total_latency_seconds": total_latency,
        "termination_reason": termination_reason,
        "selected_tool": selected_tool,
        "tool_status": tool_status,
        "error_code": error_code,
        "exception_type": exception_type,
        "completed": completed,
        "protocol_valid": protocol_valid,
        "stream_succeeded": stream_succeeded,
        "normal_termination": termination_reason
        in {"final_answer", "single_step"},
        "mixed_model_output": error_code == "mixed_model_output",
        "protocol_errors": protocol_errors,
    }


def _rate(count: int, total: int) -> float:
    return round(count / total, 3) if total else 0.0


def _latency_metrics(
    results: list[dict[str, Any]],
    field: str,
) -> tuple[float, float]:
    values = [
        float(result[field])
        for result in results
        if isinstance(result.get(field), (int, float))
        and not isinstance(result.get(field), bool)
    ]
    if not values:
        return 0.0, 0.0

    return (
        round(sum(values) / len(values), 3),
        round(nearest_rank_percentile(values, 0.95), 3),
    )


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    completed = sum(bool(result.get("completed")) for result in results)
    protocol_valid = sum(
        bool(result.get("protocol_valid")) for result in results
    )
    stream_succeeded = sum(
        bool(result.get("stream_succeeded")) for result in results
    )
    normal_termination = sum(
        bool(result.get("normal_termination")) for result in results
    )
    mixed_model_output = sum(
        bool(result.get("mixed_model_output")) for result in results
    )
    error_counts = Counter(
        str(result["error_code"])
        for result in results
        if isinstance(result.get("error_code"), str)
    )

    average_first_event, p95_first_event = _latency_metrics(
        results,
        "first_event_latency_seconds",
    )
    average_first_answer, p95_first_answer = _latency_metrics(
        results,
        "first_answer_latency_seconds",
    )
    average_total, p95_total = _latency_metrics(
        results,
        "total_latency_seconds",
    )

    return {
        "total": total,
        "completed_count": completed,
        "completion_rate": _rate(completed, total),
        "protocol_valid_count": protocol_valid,
        "protocol_valid_rate": _rate(protocol_valid, total),
        "stream_succeeded_count": stream_succeeded,
        "stream_success_rate": _rate(stream_succeeded, total),
        "normal_termination_count": normal_termination,
        "normal_termination_rate": _rate(normal_termination, total),
        "mixed_model_output_count": mixed_model_output,
        "mixed_model_output_rate": _rate(mixed_model_output, total),
        "protocol_invalid_count": total - protocol_valid,
        "error_code_counts": dict(sorted(error_counts.items())),
        "average_first_event_latency_seconds": average_first_event,
        "p95_first_event_latency_seconds": p95_first_event,
        "average_first_answer_latency_seconds": average_first_answer,
        "p95_first_answer_latency_seconds": p95_first_answer,
        "average_total_latency_seconds": average_total,
        "p95_total_latency_seconds": p95_total,
    }


def run_evaluation(
    cases: list[AgentEvalCase],
    evaluate_case_fn: Callable[[AgentEvalCase], dict[str, Any]] = (
        evaluate_stream_case
    ),
    now: datetime | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        print(
            f"Evaluating Agent stream case {index}/{len(cases)}: {case.id}",
            flush=True,
        )
        result = evaluate_case_fn(case)
        results.append(result)
        print(
            (
                f"completed {case.id} "
                f"success={result['stream_succeeded']} "
                f"protocol_valid={result['protocol_valid']} "
                f"total={result['total_latency_seconds']}s"
            ),
            flush=True,
        )

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "run_id": timestamp.strftime("%Y%m%d-%H%M%S"),
        "generated_at": utc_timestamp(timestamp),
        "summary": summarize_results(results),
        "cases": results,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Agent streaming stability and latency.",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="Path to the Agent evaluation cases JSON file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for timestamped stream evaluation reports.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_evaluation(load_cases(args.cases))
    output_path = write_report(report, output_dir=args.output_dir)
    summary = report["summary"]

    print(f"Agent stream evaluation report: {output_path}")
    print(
        (
            f"success={summary['stream_succeeded_count']} "
            f"mixed={summary['mixed_model_output_count']} "
            f"protocol_invalid={summary['protocol_invalid_count']} "
            f"total={summary['total']}"
        )
    )
    return 1 if summary["protocol_invalid_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
