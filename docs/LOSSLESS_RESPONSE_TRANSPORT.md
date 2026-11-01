# Lossless response transport adapter

The optional `portable.response_delivery_transport.deliver_response` adapter is for host consumers whose output protocol explicitly supports chunked delivery. It reuses `portable.response_delivery` and `ProgressJournal`; it does not create another response store.

## Guarantees

- UTF-8 byte-bounded chunks are sent synchronously, so the receiver call provides backpressure and at most one chunk is in flight.
- Acknowledgements must match response digest, chunk index, and chunk digest exactly.
- The journal cursor advances only after that exact acknowledgement.
- A restart resumes after the last contiguous acknowledgement.
- Cancellation, deadline, and shared byte-budget checks run before each send.
- An exception, missing acknowledgement, or mismatched acknowledgement is recorded as an unknown outcome. The same task/response is blocked from automatic replay. The receiver must provide an independently checked status/idempotency resolution before an operator starts a new delivery task.
- Receipts expose byte/chunk counts, peak in-flight bytes, resume cursor and elapsed time without response contents.

## Explicit non-goals

Chunked delivery is now integrated at the local Engineering Console HTTP receiver boundary, with explicit next/status/ack/complete/cancel routes and durable supervisor ownership. The CLI also exposes bounded stdout and atomic file sinks; stdout is not resumable and a partial pipe write is reported as an unknown outcome, while file output uses a same-directory temporary file and atomic commit. MCP stdio remains an atomic framed protocol and must continue to reject oversized frames and terminate the process tree; it must not use response chunking unless the MCP tool itself defines a separate chunk protocol. Adapters that return a single structured JSON object, evaluator verdicts, security/policy evidence, migrations, rollback records, or release decisions remain atomic and fail closed when they exceed their configured limits.

No sampling is permitted for acceptance, security, policy, evaluator, holdout, migration, rollback, or release work. Summaries must retain evidence references and disclose omissions. If the consumer cannot accept lossless chunks or the response exceeds the total budget, stop with a blocker rather than truncate.

## Rollback

Remove the adapter and its tests together. Keep the existing chunker, byte caps, MCP frame rejection, process-tree cleanup, and acknowledgement journal. Never restore unbounded buffering or silent truncation.


## Host delivery inventory update

The local Engineering Console HTTP API now uses a bounded incremental JSON encoder with a 4,000,000-byte UTF-8 ceiling. It does not silently truncate a response. Oversized API results return a small 502 error that explicitly says the endpoint is atomic and does not support chunk acknowledgements. The response headers expose the same disposition:

- `X-AUREN-Delivery-Mode: atomic-json`
- `X-AUREN-Chunk-Ack: unsupported`

This is an explicit fail-closed disposition, not a claim that the dashboard is resumable. The browser currently consumes one complete JSON response and has no receiver-side status/ack contract. Do not send chunks over this endpoint or advance a durable cursor based only on a socket write.

MCP stdio remains an atomic JSON-RPC frame. Oversized frames continue to be rejected and the child process tree is terminated; chunking is not applied to the frame. Read-only MCP pagination is a query-level adaptation, not response-delivery acknowledgement.

The HTTP API now supplies the receiver acknowledgement contract; the dashboard integration has restart/resume and stale/blocked/duplicate-ack coverage. The reusable transport can also consume the graph's shared RuntimeExecutionGuard delivery-byte budget. auren deliver provides a bounded CLI contract for stdin/stdout and file output. #1140 remains open for broader live receiver load, deployment-level queue quotas, and independent operational evidence.

## Shared crew budget and CLI sinks — 2026-11-01

RuntimeExecutionGuard now carries a checkpointable UTF-8 delivery-byte ledger alongside step, attempt, deadline and cancellation state. The transport adapter accepts the same guard instance used by graph/crew execution and charges each response/chunk identity before sending. Reusing a charged chunk key does not authorize another send. The legacy callback remains for compatibility, but callers must not supply both budget owners.

CLI examples:

```bash
# Copy a bounded stdin payload to stdout (receipt is written to stderr)
python auren_cli.py deliver --input - --output - --max-bytes 8000000

# Commit a file atomically; existing files are not replaced unless --overwrite is supplied
python auren_cli.py deliver --input result.json --output ./out/result.json --max-bytes 8000000
```

The Session Control Center /api/sessions and /api/control-center responses now include delivery worker capacity, durable task counts, acknowledged chunk count, unknown outcomes, and receiver-acknowledged delivery count. Metrics are projections from the execution controller and progress journal; the in-process saturation counter is operational telemetry, not an audit ledger.

A paired baseline/candidate benchmark is available at benchmarks/response_delivery_comparison.py. CI uploads the report as an artifact. It records integrity, recovery, latency, peak traced memory and transported-byte cost proxy. Monetary provider cost is explicitly null because the local benchmark has no metered provider. No performance claim should be made until the exact-head benchmark artifact is inspected.
