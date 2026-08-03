from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from agent_app.scripts.evaluate_agent import (
    AGENT_PROJECT_ROOT,
    DEFAULT_CASES_PATH,
    AgentEvalCase,
    extract_tool_names,
    load_cases,
    nearest_rank_percentile,
)
from agent_app.service import run_agent
from rag_app.config import config
from rag_app.evaluation.answer_judge import judge_answer
from rag_app.evaluation.judge_schema import (
    PASSING_SCORE_THRESHOLD,
    AnswerJudgeResult,
)
from rag_app.infrastructure.llm_client import get_client


DEFAULT_OUTPUT_DIR = AGENT_PROJECT_ROOT / "experiments" / "runs" / "judge"

# 输入校验类 case 验证的是空输入降级行为，没有可评的答案质量，
# 用 RAG 的 groundedness 尺子量它只会得到无意义的失败。
JUDGE_SKIPPED_TASK_TYPES = {"input_validation"}

ALL_SCORED_DIMENSIONS = (
    "relevance_score",
    "completeness_score",
    "groundedness_score",
    "format_score",
)

# direct 类问题（如打招呼）不依赖任何证据，groundedness 对它没有意义：
# 同一个回答在不同轮次会被打成 3 或 5，纯粹是噪声。
SCORED_DIMENSIONS_BY_TASK_TYPE = {
    "direct": (
        "relevance_score",
        "completeness_score",
        "format_score",
    ),
}


def scored_dimensions(task_type: str) -> tuple[str, ...]:
    return SCORED_DIMENSIONS_BY_TASK_TYPE.get(task_type, ALL_SCORED_DIMENSIONS)


def judge_passes(
    judge_result: AnswerJudgeResult,
    task_type: str,
) -> bool:
    scores = judge_result.model_dump()
    return all(
        scores[dimension] >= PASSING_SCORE_THRESHOLD
        for dimension in scored_dimensions(task_type)
    )


def build_judge_sources(
    case: AgentEvalCase,
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """总结类问题的依据是用户在提问里给出的原文，不是检索结果。"""
    if case.task_type == "summary":
        return [
            {
                "source": "user_input",
                "section_path": "question",
                "snippet": case.question,
            }
        ]

    return sources


def extract_answer(result: Any) -> str:
    output = result.tool_result.output
    if not isinstance(output, dict):
        return ""

    answer = output.get("answer")
    return answer if isinstance(answer, str) else ""


def extract_sources(result: Any) -> list[dict[str, Any]]:
    output = result.tool_result.output
    if not isinstance(output, dict):
        return []

    sources = output.get("sources")
    if not isinstance(sources, list):
        return []

    return [source for source in sources if isinstance(source, dict)]


def sanitize_sources(
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            key: value
            for key, value in source.items()
            if key != "snippet"
        }
        for source in sources
    ]


def evaluate_case(
    case: AgentEvalCase,
    judge_llm: BaseChatModel,
    run_agent_fn: Callable[[str], Any] = run_agent,
    judge_fn: Callable[..., AnswerJudgeResult] = judge_answer,
    timer: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    agent_started_at = timer()
    try:
        agent_result = run_agent_fn(case.question)
    except Exception as error:
        agent_duration_seconds = round(timer() - agent_started_at, 3)
        return {
            "id": case.id,
            "question": case.question,
            "task_type": case.task_type,
            "answer": "",
            "sources": [],
            "termination_reason": "error",
            "tools": [],
            "step_count": 0,
            "judge": None,
            "judge_applicable": True,
            "passed": False,
            "failure_stage": "agent",
            "error": {"type": type(error).__name__},
            "agent_duration_seconds": agent_duration_seconds,
            "judge_duration_seconds": 0.0,
            "total_duration_seconds": agent_duration_seconds,
        }

    agent_duration_seconds = round(timer() - agent_started_at, 3)

    answer = extract_answer(agent_result)
    sources = extract_sources(agent_result)
    common_result = {
        "id": case.id,
        "question": case.question,
        "task_type": case.task_type,
        "answer": answer,
        "sources": sanitize_sources(sources),
        "termination_reason": agent_result.termination_reason,
        "tools": extract_tool_names(agent_result),
        "step_count": len(agent_result.steps),
    }

    if case.task_type in JUDGE_SKIPPED_TASK_TYPES:
        return {
            **common_result,
            "judge": None,
            "judge_applicable": False,
            "passed": None,
            "failure_stage": None,
            "error": None,
            "agent_duration_seconds": agent_duration_seconds,
            "judge_duration_seconds": 0.0,
            "total_duration_seconds": agent_duration_seconds,
        }

    if not answer.strip():
        return {
            **common_result,
            "judge": None,
            "judge_applicable": True,
            "passed": False,
            "failure_stage": "empty_answer",
            "error": None,
            "agent_duration_seconds": agent_duration_seconds,
            "judge_duration_seconds": 0.0,
            "total_duration_seconds": agent_duration_seconds,
        }

    judge_started_at = timer()
    try:
        judge_result = judge_fn(
            question=case.question,
            answer=answer,
            sources=build_judge_sources(case, sources),
            llm=judge_llm,
        )
    except Exception as error:
        judge_duration_seconds = round(timer() - judge_started_at, 3)
        return {
            **common_result,
            "judge": None,
            "judge_applicable": True,
            "passed": False,
            "failure_stage": "judge",
            "error": {"type": type(error).__name__},
            "agent_duration_seconds": agent_duration_seconds,
            "judge_duration_seconds": judge_duration_seconds,
            "total_duration_seconds": round(
                agent_duration_seconds + judge_duration_seconds,
                3,
            ),
        }

    judge_duration_seconds = round(timer() - judge_started_at, 3)

    return {
        **common_result,
        "judge": judge_result.model_dump(),
        "judge_applicable": True,
        "scored_dimensions": list(scored_dimensions(case.task_type)),
        "passed": judge_passes(judge_result, case.task_type),
        "failure_stage": None,
        "error": None,
        "agent_duration_seconds": agent_duration_seconds,
        "judge_duration_seconds": judge_duration_seconds,
        "total_duration_seconds": round(
            agent_duration_seconds + judge_duration_seconds,
            3,
        ),
    }


def summarize_by_task_type(
    judged_results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    breakdown: dict[str, dict[str, Any]] = {}

    for result in judged_results:
        task_type = str(result.get("task_type", "unknown"))
        bucket = breakdown.setdefault(task_type, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(bool(result.get("passed")))

    for bucket in breakdown.values():
        bucket["pass_rate"] = round(bucket["passed"] / bucket["total"], 3)

    return breakdown


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    judged_results = [
        result for result in results if result.get("judge_applicable", True)
    ]
    judged_total = len(judged_results)
    score_names = {
        "relevance": "relevance_score",
        "completeness": "completeness_score",
        "groundedness": "groundedness_score",
        "format": "format_score",
    }
    valid_judges = [
        result["judge"]
        for result in results
        if isinstance(result.get("judge"), dict)
    ]

    average_scores = {
        name: (
            round(
                sum(float(judge[field]) for judge in valid_judges)
                / len(valid_judges),
                3,
            )
            if valid_judges
            else 0.0
        )
        for name, field in score_names.items()
    }

    def duration_metrics(
        field: str,
        metric_results: list[dict[str, Any]] = results,
    ) -> tuple[float, float]:
        values = [float(result[field]) for result in metric_results]
        if not values:
            return 0.0, 0.0
        return (
            round(sum(values) / len(values), 3),
            round(nearest_rank_percentile(values, 0.95), 3),
        )

    average_agent, p95_agent = duration_metrics("agent_duration_seconds")
    judge_attempted_results = [
        result
        for result in results
        if isinstance(result.get("judge"), dict)
        or result.get("failure_stage") == "judge"
    ]
    average_judge, p95_judge = duration_metrics(
        "judge_duration_seconds",
        judge_attempted_results,
    )
    average_total, p95_total = duration_metrics("total_duration_seconds")
    passed = sum(bool(result.get("passed")) for result in judged_results)

    return {
        "total": total,
        "judged_total": judged_total,
        "skipped_total": total - judged_total,
        "passed": passed,
        "failed": judged_total - passed,
        "pass_rate": (
            round(passed / judged_total, 3) if judged_total else 0.0
        ),
        "pass_rate_by_task_type": summarize_by_task_type(judged_results),
        "average_scores": average_scores,
        "average_agent_duration_seconds": average_agent,
        "p95_agent_duration_seconds": p95_agent,
        "average_judge_duration_seconds": average_judge,
        "p95_judge_duration_seconds": p95_judge,
        "average_total_duration_seconds": average_total,
        "p95_total_duration_seconds": p95_total,
        "agent_failed_count": sum(
            result.get("failure_stage") == "agent"
            for result in results
        ),
        "judge_failed_count": sum(
            result.get("failure_stage") == "judge"
            for result in results
        ),
    }


def run_evaluation(
    cases: list[AgentEvalCase],
    judge_llm: BaseChatModel,
    judge_model_id: str,
    evaluate_case_fn: Callable[..., dict[str, Any]] = evaluate_case,
    now: datetime | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        print(
            f"Evaluating Agent Judge case {index}/{len(cases)}: {case.id}",
            flush=True,
        )
        result = evaluate_case_fn(case, judge_llm=judge_llm)
        results.append(result)
        print(
            (
                f"completed {case.id} passed={result['passed']} "
                f"total={result['total_duration_seconds']}s"
            ),
            flush=True,
        )

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "run_id": timestamp.strftime("%Y%m%d-%H%M%S"),
        "judge_model_id": judge_model_id,
        "judge_independence": "same_model",
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
        description="Evaluate Agent final answers with the configured LLM Judge.",
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
        help="Directory for timestamped Agent Judge reports.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = load_cases(args.cases)
    judge_llm = get_client()
    judge_model_id = config.settings.llm_model_id or "unknown"
    report = run_evaluation(
        cases,
        judge_llm=judge_llm,
        judge_model_id=judge_model_id,
    )
    output_path = write_report(report, output_dir=args.output_dir)
    summary = report["summary"]

    print(f"Agent Judge report: {output_path}")
    print(
        f"passed={summary['passed']} failed={summary['failed']} "
        f"judged={summary['judged_total']} skipped={summary['skipped_total']} "
        f"total={summary['total']}"
    )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
