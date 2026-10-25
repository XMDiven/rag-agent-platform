"""Exercise the real comparison paths through a bounded, offline SSE transport."""

import json
from types import SimpleNamespace

import httpx
from langchain_core.documents import Document

from agent_app.prompts import AGENT_LOOP_SYSTEM_PROMPT
from agent_app.scripts.compare_strategies import run_comparison
from agent_app.scripts.comparison_budget import SharedQuota
from rag_app.config import config
from rag_app.scripts.evaluate_retrieval import RetrievalEvalCase
from rag_app.services import ask_service


def setup_transport(monkeypatch):
    monkeypatch.setattr(config.settings, "moonshot_api_key", "offline-key")
    monkeypatch.setattr(config.settings, "llm_base_url", "https://offline.invalid/v1")
    monkeypatch.setattr(config.settings, "llm_model_id", "offline-model")
    retrieved = []

    def retrieve(question):
        retrieved.append(question)
        return [Document(page_content="Original evidence.", metadata={"source": "one.md"})]

    monkeypatch.setattr(ask_service, "get_retriever", lambda **kwargs: SimpleNamespace(invoke=retrieve))
    sent = []

    def send(request):
        body = json.loads(request.content)
        sent.append(body)
        messages = body["messages"]
        system = messages[0]["content"]
        tools = system == AGENT_LOOP_SYSTEM_PROMPT and not any(m["role"] == "tool" for m in messages)
        text = "Offline answer [1]."
        if "answer quality judge" in system:
            text = json.dumps(dict(relevance_score=5, completeness_score=5, groundedness_score=5,
                                   format_score=5, overall_pass=True, feedback="offline"))
        delta = {"content": "" if tools else text}
        if tools:
            delta["tool_calls"] = [{"index": 0, "id": "call_x", "type": "function", "function": {
                "name": "question_decompose_tool", "arguments": json.dumps({"question": "A 和 B 分别适合做什么？"}),
            }}]
        events = [
            {"id": "x", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
            {"id": "x", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"id": "x", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tools else "stop"}]},
            {"id": "x", "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        ]
        data = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        return httpx.Response(200, stream=httpx.ByteStream(data.encode()), headers={"content-type": "text/event-stream"})

    return sent, retrieved, lambda: httpx.MockTransport(send)


def test_three_real_paths_use_guarded_clients_for_generation_and_judge(monkeypatch):
    sent, retrieved, factory = setup_transport(monkeypatch)
    case = RetrievalEvalCase("offline", "A 和 B 分别适合做什么？", ["one.md"])
    report = run_comparison([case], transport_factory=factory)
    assert report["summary"] == {"total": 3, "passed": 3, "failed": 0}
    assert [row["model_requests"] for row in report["cases"]] == [1, 3, 4]
    assert [row["judge_requests"] for row in report["cases"]] == [1, 1, 1]
    assert [row["rag_calls"] for row in report["cases"]] == [1, 2, 2]
    assert [row["retrieval_calls"] for row in report["cases"]] == [1, 2, 2]
    assert len(sent) == report["quota"]["used_calls"] == 11
    assert len(retrieved) == 5
    assert all(body["max_tokens"] <= 1024 and body["stream"] for body in sent)
    assert all(row["final_answer_first_token_seconds"] is not None for row in report["cases"])
    assert all(row["output_tokens"] == row["model_requests"] * 5 for row in report["cases"])
    assert all(row["judge_usage"]["output_tokens"] == 5 for row in report["cases"])


def test_shared_exhaustion_stops_later_rows_before_retrieval(monkeypatch):
    sent, retrieved, factory = setup_transport(monkeypatch)
    case = RetrievalEvalCase("offline", "A 和 B 分别适合做什么？", ["one.md"])
    report = run_comparison([case], quota=SharedQuota(max_calls=1), transport_factory=factory)
    assert report["summary"]["failed"] == 3
    assert [row["failure_stage"] for row in report["cases"]] == ["judge_budget", "budget", "budget"]
    assert len(sent) == len(retrieved) == 1


def test_failed_requests_have_unknown_cost_even_with_configured_prices(monkeypatch):
    setup_transport(monkeypatch)
    for name in ("LLM_INPUT_PRICE_PER_MILLION", "LLM_CACHED_INPUT_PRICE_PER_MILLION", "LLM_OUTPUT_PRICE_PER_MILLION"):
        monkeypatch.setattr(config, name, 1.0)
    case = RetrievalEvalCase("offline", "A 和 B 分别适合做什么？", ["one.md"])
    report = run_comparison([case], transport_factory=lambda: httpx.MockTransport(
        lambda request: httpx.Response(500, json={"error": {"message": "offline failure"}}),
    ))
    assert report["summary"]["failed"] == 3
    assert all(row["model_requests"] > 0 for row in report["cases"])
    assert all(row["cost"] is None for row in report["cases"])


def test_missing_usage_never_reports_zero_cost(monkeypatch):
    _, _, factory = setup_transport(monkeypatch)
    for name in ("LLM_INPUT_PRICE_PER_MILLION", "LLM_CACHED_INPUT_PRICE_PER_MILLION", "LLM_OUTPUT_PRICE_PER_MILLION"):
        monkeypatch.setattr(config, name, 1.0)

    def send(request):
        original = factory().handle_request(request)
        content = b"\n".join(line for line in original.read().split(b"\n") if b'"usage"' not in line)
        return httpx.Response(200, stream=httpx.ByteStream(content), headers={"content-type": "text/event-stream"})

    report = run_comparison([RetrievalEvalCase("x", "A 和 B 分别适合做什么？", ["one.md"])],
                            transport_factory=lambda: httpx.MockTransport(send))
    assert all(row["cost"] is None and row["judge_cost"] is None for row in report["cases"])
    assert all(group["generation_cost"] is None for group in report["statistics"]["by_strategy"].values())


def test_complete_usage_with_configured_prices_reports_separate_costs(monkeypatch):
    _, _, factory = setup_transport(monkeypatch)
    for name in ("LLM_INPUT_PRICE_PER_MILLION", "LLM_CACHED_INPUT_PRICE_PER_MILLION", "LLM_OUTPUT_PRICE_PER_MILLION"):
        monkeypatch.setattr(config, name, 1.0)
    report = run_comparison([RetrievalEvalCase("x", "A 和 B 分别适合做什么？", ["one.md"])], transport_factory=factory)
    for row in report["cases"]:
        assert row["cost"] == round(row["model_requests"] * 15 / 1_000_000, 6)
        assert row["judge_cost"] == 0.000015
