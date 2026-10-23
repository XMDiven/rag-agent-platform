# Streaming cancellation

The frontend BFF forwards the request AbortSignal to the Agent HTTP request.
The Agent monitors disconnects while consuming an asynchronous model stream,
including before the first token arrives. Disconnect or response-task cancellation
cancels the pending read, closes the model iterator, and stops further Agent rounds.
Cancellation is not treated as a failure requiring fallback generation.

Synchronous tools run in a worker with a request-scoped cooperative cancellation
event. Disconnect stops waiting for that worker; decomposed retrieval checks the
event before each sub-question so remaining sub-questions are skipped.
An already-running synchronous retrieval/generation cannot be forcibly stopped:
it finishes or hits its existing upstream timeout. Closing a model connection also
does not guarantee that a provider immediately stops computation or billing.

The CLI, orchestration evaluator (`evaluate_agent`), and answer Judge
(`evaluate_agent_answers`) use the synchronous `run_agent()` entry point, which can
fall back to single-step planning after an unhandled loop exception. HTTP streaming
and the current streaming evaluator (`evaluate_agent_stream`) use the async path and
report generation errors as error/done events, without starting a second synchronous
fallback request. The optional fine-tuned router participates only in non-empty
single-step planning when configured; normal loops and HTTP stream failures do not
call it. The legacy synchronous stream generator remains for internal callers and
unit tests.

Verification: `uv run --frozen pytest agent/tests/test_stream_cancellation.py -q`
covers a disconnect before the first model token, cancellation during a synchronous
tool, skipping remaining sub-questions, and normal stream event order. The frontend
route tests cover propagation of the AbortSignal to the upstream fetch.
