from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rag_app.config import config
from rag_app.scripts.evaluate_answers_with_judge import (
    run_evaluation,
    save_report,
)

JUDGE_RUNS_DIR = config.PROJECT_ROOT / "experiments" / "runs" / "judge"


def list_report_files() -> list[Path]:
    if not JUDGE_RUNS_DIR.exists():
        return []

    return sorted(JUDGE_RUNS_DIR.glob("*.json"))


def parse_created_at(run_id: str) -> str:
    try:
        return datetime.strptime(run_id, "%Y%m%d-%H%M%S").isoformat()
    except ValueError:
        return run_id


def build_report_path(run_id: str) -> Path:
    report_path = JUDGE_RUNS_DIR / f"{run_id}.json"

    if report_path.resolve().parent != JUDGE_RUNS_DIR.resolve():
        raise FileNotFoundError(f"judge report not found: {run_id}")

    return report_path


def load_report(run_id: str) -> dict[str, Any]:
    report_path = build_report_path(run_id)

    if not report_path.exists():
        raise FileNotFoundError(f"judge report not found: {run_id}")

    return json.loads(report_path.read_text(encoding="utf-8"))


def list_reports() -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []

    for report_file in list_report_files():
        report = json.loads(report_file.read_text(encoding="utf-8"))
        run_id = str(report.get("run_id", report_file.stem))

        reports.append(
            {
                "run_id": run_id,
                "prompt_version": report.get("prompt_version", ""),
                "total": report.get("total", 0),
                "passed": report.get("passed", 0),
                "failed": report.get("failed", 0),
                "created_at": parse_created_at(run_id),
            }
        )

    return reports


def average_case_value(cases: list[dict[str, Any]], key: str) -> float:
    values: list[float] = []

    for case in cases:
        judge = case.get("judge") or {}
        value = case.get(key, judge.get(key))

        if isinstance(value, (int, float)):
            values.append(float(value))

    if not values:
        return 0.0

    return round(sum(values) / len(values), 2)


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    cases = report.get("cases", [])

    return {
        "run_id": report.get("run_id", ""),
        "prompt_version": report.get("prompt_version", ""),
        "total": report.get("total", 0),
        "passed": report.get("passed", 0),
        "failed": report.get("failed", 0),
        "avg_relevance": average_case_value(cases, "relevance_score"),
        "avg_completeness": average_case_value(cases, "completeness_score"),
        "avg_groundedness": average_case_value(cases, "groundedness_score"),
        "avg_format": average_case_value(cases, "format_score"),
        "avg_answer_duration": average_case_value(
            cases,
            "answer_duration_seconds",
        ),
        "avg_judge_duration": average_case_value(
            cases,
            "judge_duration_seconds",
        ),
        "avg_total_duration": average_case_value(
            cases,
            "total_duration_seconds",
        ),
    }


def find_latest_report_by_prompt(prompt_version: str) -> dict[str, Any] | None:
    reports: list[dict[str, Any]] = []

    for report_file in list_report_files():
        report = json.loads(report_file.read_text(encoding="utf-8"))

        if report.get("prompt_version") == prompt_version:
            reports.append(report)

    if not reports:
        return None

    return sorted(
        reports,
        key=lambda report: str(report.get("run_id", "")),
    )[-1]


def get_latest_comparison() -> dict[str, Any]:
    v1_report = find_latest_report_by_prompt("qa_prompt_v1")
    v2_report = find_latest_report_by_prompt("qa_prompt_v2")

    if v1_report is None or v2_report is None:
        raise FileNotFoundError("missing qa_prompt_v1 or qa_prompt_v2 judge report")

    return {
        "qa_prompt_v1": summarize_report(v1_report),
        "qa_prompt_v2": summarize_report(v2_report),
        "selected_prompt": config.QA_PROMPT_VERSION,
        "decision_reason": (
            "Use the configured default prompt after comparing judge scores "
            "and latency metrics."
        ),
    }


def run_prompt_eval(
    prompt_version: str,
    case_limit: int | None = None,
    cases: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    report = run_evaluation(
        prompt_version=prompt_version,
        case_limit=case_limit,
        cases=cases,
    )

    save_report(report, output_dir=JUDGE_RUNS_DIR)

    run_id = str(report["run_id"])

    return {
        "run_id": run_id,
        "prompt_version": report["prompt_version"],
        "status": "completed",
        "total": report["total"],
        "passed": report["passed"],
        "failed": report["failed"],
        "report_url": f"/prompt-evals/reports/{run_id}",
    }
