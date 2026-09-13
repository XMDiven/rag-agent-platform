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

The synchronous CLI/evaluation entry point remains available. HTTP streaming uses
the async path and reports generation errors as error/done events, without starting
a second synchronous fallback request.

Verification: `uv run --frozen pytest agent/tests/test_stream_cancellation.py -q`
covers a disconnect before the first model token, cancellation during a synchronous
tool, skipping remaining sub-questions, and normal stream event order. The frontend
route tests cover propagation of the AbortSignal to the upstream fetch.
