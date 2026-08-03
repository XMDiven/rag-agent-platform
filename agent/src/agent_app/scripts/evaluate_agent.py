from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from agent_app.service import run_agent


AGENT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES_PATH = (
    AGENT_PROJECT_ROOT / "experiments" / "datasets" / "agent_eval_cases.json"
)
DEFAULT_OUTPUT_DIR = AGENT_PROJECT_ROOT / "experiments" / "runs" / "evaluation"

CASE_FIELDS = {
    "id",
    "question",
    "allowed_tools",
    "required_tools",
    "allowed_termination_reasons",
    "requires_sources",
    "max_steps",
}


@dataclass(frozen=True)
class AgentEvalCase:
    id: str
    question: str
    allowed_tools: list[str]
    required_tools: list[str]
    allowed_termination_reasons: list[str]
    requires_sources: bool
    max_steps: int


def validate_string_list(value: Any, field: str, label: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item
        for item in value
    ):
        raise ValueError(f"{label}: {field} must be a list of non-empty strings")

    return list(value)


def parse_case(raw: Any, index: int) -> AgentEvalCase:
    label = f"case at index {index}"
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")

    case_id = raw.get("id")
    if isinstance(case_id, str) and case_id:
        label = f"case {case_id}"

    unknown_fields = set(raw) - CASE_FIELDS
    missing_fields = CASE_FIELDS - set(raw)
    if unknown_fields:
        raise ValueError(f"{label}: unknown fields: {sorted(unknown_fields)}")
    if missing_fields:
        raise ValueError(f"{label}: missing fields: {sorted(missing_fields)}")

    if not isinstance(case_id, str) or not case_id:
        raise ValueError(f"{label}: id must be a non-empty string")

    question = raw["question"]
    if not isinstance(question, str):
        raise ValueError(f"{label}: question must be a string")

    allowed_tools = validate_string_list(
        raw["allowed_tools"],
        "allowed_tools",
        label,
    )
    required_tools = validate_string_list(
        raw["required_tools"],
        "required_tools",
        label,
    )
    allowed_termination_reasons = validate_string_list(
        raw["allowed_termination_reasons"],
        "allowed_termination_reasons",
        label,
    )
    if not allowed_termination_reasons:
        raise ValueError(
            f"{label}: allowed_termination_reasons must not be empty"
        )

    missing_allowed_tools = set(required_tools) - set(allowed_tools)
    if missing_allowed_tools:
        raise ValueError(
            f"{label}: required_tools must be included in allowed_tools"
        )

    requires_sources = raw["requires_sources"]
    if not isinstance(requires_sources, bool):
        raise ValueError(f"{label}: requires_sources must be a boolean")

    max_steps = raw["max_steps"]
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 0:
        raise ValueError(f"{label}: max_steps must be a non-negative integer")

    return AgentEvalCase(
        id=case_id,
        question=question,
        allowed_tools=allowed_tools,
        required_tools=required_tools,
        allowed_termination_reasons=allowed_termination_reasons,
        requires_sources=requires_sources,
        max_steps=max_steps,
    )


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[AgentEvalCase]:
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_cases, list):
        raise ValueError(f"{path}: dataset root must be a list")
    if not raw_cases:
        raise ValueError(f"{path}: dataset must contain at least one case")

    cases = [parse_case(raw_case, index) for index, raw_case in enumerate(raw_cases)]
    seen_ids: set[str] = set()
    for case in cases:
        if case.id in seen_ids:
            raise ValueError(f"duplicate case id: {case.id}")
        seen_ids.add(case.id)

    return cases


def extract_tool_names(result: Any) -> list[str]:
    steps = result.steps if isinstance(result.steps, list) else []
    tool_names = [
        str(step["tool_name"])
        for step in steps
        if isinstance(step, dict) and step.get("tool_name")
    ]

    if tool_names or result.termination_reason != "single_step":
        return tool_names

    selected_tool = getattr(getattr(result.plan, "tool", None), "name", None)
    return [str(selected_tool)] if selected_tool else []


def evaluate_result(case: AgentEvalCase, result: Any) -> dict[str, Any]:
    tools = extract_tool_names(result)
    tool_output = result.tool_result.output
    sources = tool_output.get("sources", []) if isinstance(tool_output, dict) else []
    source_count = len(sources) if isinstance(sources, list) else 0
    steps = result.steps if isinstance(result.steps, list) else []

    allowed_tools_passed = all(tool in case.allowed_tools for tool in tools)
    required_tools_passed = all(tool in tools for tool in case.required_tools)
    termination_passed = (
        result.termination_reason in case.allowed_termination_reasons
    )
    sources_passed = not case.requires_sources or source_count > 0
    steps_passed = len(steps) <= case.max_steps

    failure_reasons: list[str] = []
    if not allowed_tools_passed:
        failure_reasons.append("disallowed_tool")
    if not required_tools_passed:
        failure_reasons.append("missing_required_tool")
    if not termination_passed:
        failure_reasons.append("unexpected_termination_reason")
    if not sources_passed:
        failure_reasons.append("missing_sources")
    if not steps_passed:
        failure_reasons.append("max_steps_exceeded")

    checks = {
        "tools": allowed_tools_passed and required_tools_passed,
        "termination": termination_passed,
        "sources": sources_passed,
        "steps": steps_passed,
    }

    return {
        "id": case.id,
        "question": case.question,
        "expected": asdict(case),
        "actual": {
            "tools": tools,
            "termination_reason": result.termination_reason,
            "source_count": source_count,
            "step_count": len(steps),
        },
        "checks": checks,
        "passed": all(checks.values()),
        "failure_reasons": failure_reasons,
    }


def nearest_rank_percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0

    ordered_values = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered_values)))
    return ordered_values[min(rank - 1, len(ordered_values) - 1)]


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    if not total:
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "pass_rate": 0.0,
            "normal_termination_rate": 0.0,
            "tool_constraint_pass_rate": 0.0,
            "source_constraint_pass_rate": 0.0,
            "average_latency_seconds": 0.0,
            "p95_latency_seconds": 0.0,
        }

    passed = sum(bool(result["passed"]) for result in results)
    normal_terminations = sum(
        result.get("actual", {}).get("termination_reason")
        in {"final_answer", "single_step"}
        for result in results
    )
    tool_constraints_passed = sum(
        bool(result.get("checks", {}).get("tools"))
        for result in results
    )
    source_required_results = [
        result
        for result in results
        if result.get("expected", {}).get("requires_sources") is True
    ]
    source_constraints_passed = sum(
        bool(result.get("checks", {}).get("sources"))
        for result in source_required_results
    )
    source_constraint_pass_rate = (
        round(source_constraints_passed / len(source_required_results), 3)
        if source_required_results
        else 0.0
    )
    latencies = [float(result["latency_seconds"]) for result in results]

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 3),
        "normal_termination_rate": round(normal_terminations / total, 3),
        "tool_constraint_pass_rate": round(tool_constraints_passed / total, 3),
        "source_constraint_pass_rate": source_constraint_pass_rate,
        "average_latency_seconds": round(sum(latencies) / total, 3),
        "p95_latency_seconds": round(
            nearest_rank_percentile(latencies, 0.95),
            3,
        ),
    }


def utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def run_evaluation(
    cases: list[AgentEvalCase],
    run_agent_fn: Callable[[str], Any] = run_agent,
    timer: Callable[[], float] = perf_counter,
    now: datetime | None = None,
) -> dict[str, Any]:
    case_results: list[dict[str, Any]] = []

    for case in cases:
        started_at = timer()
        try:
            result = run_agent_fn(case.question)
            case_result = evaluate_result(case, result)
        except Exception as error:
            case_result = {
                "id": case.id,
                "question": case.question,
                "expected": asdict(case),
                "actual": {
                    "tools": [],
                    "termination_reason": "error",
                    "source_count": 0,
                    "step_count": 0,
                },
                "checks": {
                    "tools": False,
                    "termination": False,
                    "sources": not case.requires_sources,
                    "steps": True,
                },
                "passed": False,
                "failure_reasons": ["agent_error"],
                "error": {
                    "type": type(error).__name__,
                },
            }

        case_result["latency_seconds"] = round(timer() - started_at, 3)
        case_results.append(case_result)

    generated_at = now or datetime.now(timezone.utc)
    return {
        "generated_at": utc_timestamp(generated_at),
        "summary": summarize_results(case_results),
        "cases": case_results,
    }


def write_report(
    report: dict[str, Any],
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    now: datetime | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    output_path = output_dir / timestamp.strftime("%Y%m%d-%H%M%S.json")
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the multi-step Agent against a deterministic golden set.",
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
        help="Directory for timestamped evaluation reports.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = load_cases(args.cases)
    report = run_evaluation(cases)
    output_path = write_report(report, output_dir=args.output_dir)
    summary = report["summary"]

    print(f"Agent evaluation report: {output_path}")
    print(
        f"passed={summary['passed']} failed={summary['failed']} "
        f"total={summary['total']}"
    )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
