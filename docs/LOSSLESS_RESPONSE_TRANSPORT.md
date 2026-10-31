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

This is not wired into every host adapter yet. MCP stdio is an atomic framed protocol and must continue to reject oversized frames and terminate the process tree; it must not use response chunking unless the MCP tool itself defines a separate chunk protocol. Adapters that return a single structured JSON object, evaluator verdicts, security/policy evidence, migrations, rollback records, or release decisions remain atomic and fail closed when they exceed their configured limits.

No sampling is permitted for acceptance, security, policy, evaluator, holdout, migration, rollback, or release work. Summaries must retain evidence references and disclose omissions. If the consumer cannot accept lossless chunks or the response exceeds the total budget, stop with a blocker rather than truncate.

## Rollback

Remove the adapter and its tests together. Keep the existing chunker, byte caps, MCP frame rejection, process-tree cleanup, and acknowledgement journal. Never restore unbounded buffering or silent truncation.


## Host delivery inventory update

The local Engineering Console HTTP API now uses a bounded incremental JSON encoder with a 4,000,000-byte UTF-8 ceiling. It does not silently truncate a response. Oversized API results return a small 502 error that explicitly says the endpoint is atomic and does not support chunk acknowledgements. The response headers expose the same disposition:

- `X-AUREN-Delivery-Mode: atomic-json`
- `X-AUREN-Chunk-Ack: unsupported`

This is an explicit fail-closed disposition, not a claim that the dashboard is resumable. The browser currently consumes one complete JSON response and has no receiver-side status/ack contract. Do not send chunks over this endpoint or advance a durable cursor based only on a socket write.

MCP stdio remains an atomic JSON-RPC frame. Oversized frames continue to be rejected and the child process tree is terminated; chunking is not applied to the frame. Read-only MCP pagination is a query-level adaptation, not response-delivery acknowledgement.

The reusable acknowledged transport remains available for a host adapter that supplies a real receiver acknowledgement and reconciliation/status lookup. No such browser or MCP receiver contract is enabled by this change. #1140 remains open until a supported host transport is integrated and restart/resume is proven at that adapter boundary.
