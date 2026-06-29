import json
from datetime import datetime

from rag_app.config import config
from rag_app.scripts import evaluate_answers, evaluate_retrieval


def save_report(report: dict) -> None:
    output_dir = config.PROJECT_ROOT / "experiments" / "runs" / "evaluation"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{report['run_id']}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"evaluation report saved to: {output_path}")


def main() -> None:
    retrieval_summary = evaluate_retrieval.run_evaluation()
    answer_summary = evaluate_answers.run_evaluation()

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    failed_cases = [
        case
        for case in retrieval_summary["cases"] + answer_summary["cases"]
        if not case["passed"]
    ]

    report = {
        "run_id": run_id,
        "retrieval_top_k": config.RETRIEVAL_TOP_K,
        "retrieval": retrieval_summary,
        "answer": answer_summary,
        "failed_cases": failed_cases,
        "prompt_version": config.QA_PROMPT_VERSION,
    }

    save_report(report)

    if retrieval_summary["passed"] != retrieval_summary["total"]:
        raise SystemExit(1)

    if answer_summary["passed"] != answer_summary["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
