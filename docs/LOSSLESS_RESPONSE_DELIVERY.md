# Lossless response delivery

AUREN must not silently truncate code, patches, evidence, or final answers when a
model, transport, tool, or UI response budget is approached. The reusable
primitive in `portable.response_delivery` treats chunking as transport only:
it preserves the original text exactly and provides end-to-end integrity checks.

## Contract

- Use UTF-8 byte limits as well as any character/token limits imposed by the
  receiving adapter. The adapter must set `max_bytes` below its actual safe
  payload ceiling, leaving room for framing metadata.
- Prefer paragraph, line, and whitespace boundaries; split long code lines or
  unbroken strings at Unicode code-point boundaries when required.
- Emit a manifest and ordered chunk metadata: response digest, chunk count,
  chunk index, per-chunk digest, total characters, and total UTF-8 bytes.
- Resume from the last **acknowledged** chunk. Do not assume that sending a
  chunk means the user or downstream consumer received it.
- Reassemble only after validating order, uniqueness, per-chunk digest, manifest
  lengths, and the whole-response digest. Missing, duplicated, reordered, or
  corrupted chunks fail closed.
- Empty output still has an explicit completion chunk.
- Keep original artifacts (files/patches) canonical when the host supports
  attachments or durable artifact references. A text preview is not a substitute
  for a missing file. Never invent an artifact path or claim delivery without
  a verified reference.
- Never use sampling or summarization for final code, security constraints,
  acceptance criteria, failure evidence, migration/rollback instructions, or
  any task whose acceptance requires exhaustive coverage. Sampling is only an
  explicitly permitted exploration technique.
- If a platform has no acknowledgement/resume mechanism, mark delivery as
  unconfirmed and provide stable part labels plus a clear continuation cursor.
  Do not claim that the entire response was delivered.

## Integration sequence

1. Construct the complete result or artifact first; do not generate a knowingly
   incomplete final response as the canonical result.
2. Estimate the target adapter's safe output budget and chunk the result before
   serialization.
3. Deliver numbered chunks with stable response ID and total count.
4. Persist the last acknowledged index in the existing task/progress journal
   where durable delivery is required; do not create a parallel delivery store.
5. Verify receipt or reconstruct the response from the chunks and compare the
   whole-response digest.
6. Mark the task complete only after artifact validation and delivery state are
   both complete. If interrupted, resume at the next unacknowledged chunk.

## Bounded host worker admission

The HTTP delivery supervisor admits at most eight active delivery workers
across processes that share the same response-delivery registry database. Admission
uses a SQLite transaction and a renewable 30-second worker-slot lease. A worker
refreshes its lease every five seconds and releases the slot on normal exit. A host
crash leaves only a time-bounded slot; a later process can reclaim it after expiry.
The controller's own task lease still fences stale workers and prevents a second
worker from silently taking over an active task.

A saturated host leaves additional durable delivery tasks pending; it does not fail,
discard, replay, or mark them complete. A later status/next poll can retry admission
after a worker exits. The delivery status response exposes process-local and shared
worker capacity. Process-local saturation-event counts reset on restart and are
operational telemetry, not an audit ledger.

The quota is global only for processes using the same registry database. It does not
bound the number of durable pending tasks, coordinate separate registry databases,
or replace deployment-level admission controls. Persistent queue admission limits,
multi-host deployment validation, and operator-tested recovery remain separate gates.

## Shared crew budget and host sinks

The acknowledged transport accepts the same `RuntimeExecutionGuard` instance used by
the graph/crew and charges UTF-8 bytes before each chunk send. Its operation key is
bound to task, response digest, chunk index and chunk digest. The guard snapshot
preserves the byte ledger across checkpoint/resume; duplicate charge keys do not
grant a second send through this transport API.

The HTTP Session Control Center path also charges each chunk offer against a
supervisor-owned durable budget record before returning the chunk. The default
`serve()` entry point accepts the caller's `delivery_execution_guard` and stable
`delivery_budget_id`; it binds that exact guard to the delivery supervisor and
passes it into task creation. A guard without a non-empty stable ID is rejected.
Hosts may also provide only a previously persisted budget ID to resume its ledger.
Tasks using the same ID share one budget ledger. The registry stores the guard
snapshot, and restart resumes from that snapshot. If no crew budget is supplied,
the host retains a delivery-scoped budget and must not claim it shares the graph/crew
budget. Re-reading an unacknowledged chunk is
allowed without a second charge because the operation key and byte count must match.
A different chunk or delivery cannot spend beyond the remaining shared budget.
Without an injected crew guard, the supervisor creates a delivery-scoped budget.

The CLI `auren deliver` command supports bounded stdin/stdout and file output.
Oversized input is rejected before writing. A partial stdout write is reported as
unknown and must not be replayed automatically. File output is staged in the target
directory and committed atomically; existing files require explicit `--overwrite`.
Atomic MCP JSON-RPC frames remain subject to their frame limit and are never split.


## Delivery telemetry and privacy

The supervisor projects delivery measurements into the Session Control Center from the
existing progress journal. The measurement record contains only:
- total UTF-8 bytes and chunk count;
- largest in-flight chunk size observed at the receiver boundary;
- elapsed offer-to-ack wait for HTTP pull delivery, and synchronous receiver wait for
  direct transport delivery;
- worker re-admissions after the initial start, used as the resume count;
- bounded failure categories such as `shared_budget_exhausted`,
  `receiver_exception`, or `acknowledgement_rejected`;
- the SHA-256 completion digest after the receiver's final acknowledgement.

No response text, prompt, tool output, credential, or exception message is stored in
these metrics. Completion digests are evidence references, not content. Metrics events
do not change task completion state; only the existing durable acknowledgement,
artifact verification, and supervisor finalization gates can do that.


The local baseline/candidate benchmark runs five paired iterations over the same
299,008-byte payload. Latest run: [workflow 41276681840](https://github.com/sourabhJainR/AUREN/actions/runs/41276681840), five paired iterations, baseline `98730d8c9323bc1090c83fd23c1470586d882ba3`, candidate `05a63d27e5834d26c31e45bbb2564ba6fd52a770`.

| Measure | Baseline | Candidate | Change |
|---|---:|---:|---:|
| Median latency | 285.469 ms | 289.440 ms | +3.971 ms (+1.39%) |
| Median peak memory | 3,262,528 bytes | 2,182,192 bytes | -1,080,336 bytes (-33.1%) |
| Transported bytes | 299,008 | 299,008 | No change |
| Chunks | 74 | 74 | No change |
| Integrity | Pass in all runs | Pass in all runs | Preserved |
| Completion | All complete | All complete | Preserved |

The recovery experiment interrupted after two acknowledged chunks, resumed from
chunk 2, delivered the remaining 72 chunks, and observed no replay of acknowledged
chunks. Telemetry adds a small measured latency cost in this direct-transport harness
while preserving integrity, recovery and byte counts. The benchmark does not measure
the HTTP dashboard's end-to-end network path; its per-chunk journal overhead still
needs a dedicated host-level benchmark. Provider spend is reported as not measured because the local benchmark has
no metered provider; transported bytes are the cost proxy. Environment: Python
3.12.15 on Linux x86_64, five iterations. These results are descriptive and are not
a release or capability-promotion gate.

## Boundaries and rollback

This module is a deterministic transport primitive. It does not prove that a
provider or chat UI will accept every chunk, does not increase the platform's
maximum response size, and does not itself persist acknowledgements. Integrate
it through the canonical runtime and adapter lifecycle before claiming end-to-end
delivery guarantees.

Rollback: remove the adapter integration and return to the prior bounded-output
path; retain artifact integrity checks and never restore silent truncation.

## Durable resume integration

The transport primitive is paired with `ProgressJournal.record_response_chunk_ack(...)` for host workflows that deliver a response over multiple acknowledged writes. Persist only the whole-response digest, total chunk count and highest contiguous acknowledged index; keep response bytes in the canonical artifact or delivery buffer. After restart, call `response_delivery_checkpoint(...)` and pass its acknowledged index to `chunks_after(...)`. The journal rejects skipped/reordered acknowledgements, duplicate delivery is idempotent, and a changed manifest cannot reuse an old cursor. Do not mark the parent task complete until the receiver has acknowledged the final chunk and the host has verified the whole-response digest. This is a reusable durable cursor API; each host adapter must still wire its actual transport acknowledgement to it.


## Dashboard HTTP chunk protocol (opt-in)

The local dashboard now supports an explicit pull-and-ack contract when the caller sends
`Accept: application/vnd.auren.chunked+json` to `POST /api/route`. The legacy request remains
bounded atomic JSON and keeps its existing response headers.

For the chunked mode, the host serializes the complete response under a 16 MB UTF-8 budget,
stages an immutable directory containing the manifest and UTF-8 chunk files in the existing
content-addressed `ArtifactStore` under the project `.auren/releases` root, and returns a
digest-bound manifest. The response digest identifies content; a unique delivery ID isolates each
receiver session so two deliveries of identical bytes cannot share an acknowledgement cursor. A
bounded in-process cache avoids rehashing the full artifact for every chunk request; after restart, the artifact is revalidated before it is cached. The progress journal
stores only the artifact digest reference and receiver-confirmed acknowledgement cursor, never
response bytes.
The client then:

1. Fetches `GET /api/delivery/{response_id}/{delivery_id}/next`. Reads do not advance the cursor.
2. Persists the exact chunk on the receiver before calling `POST /api/delivery/{response_id}/{delivery_id}/ack`
   with the response digest, contiguous index, total count, and chunk digest.
3. Uses `GET /api/delivery/{response_id}/{delivery_id}/status` after reconnect/restart. The server returns the
   last receiver-confirmed contiguous index; a repeated ack for that same chunk is idempotent.
4. Reassembles and hashes the full response on the receiver, then calls
   `POST /api/delivery/{response_id}/{delivery_id}/complete`. The host independently verifies its immutable
   artifact and records a terminal delivery receipt only after all chunks are acknowledged.

The acknowledgement endpoint trusts the client to send an ack only after receiver-side durable
acceptance; the protocol cannot inspect the client's storage. Clients must not ack after merely
receiving bytes into a transient buffer. A blocked, recovering, stale, failed or cancelled delivery
cannot accept a late acknowledgement or be finalized by the completion endpoint. The host requires
a running durable delivery state and sequence-fences the final completion write; recovery must
reconcile the artifact and acknowledgement journal before the delivery can continue. The delivery journal is the same project-scoped
`.auren/progress.jsonl` used by `ExecutionController`; its task key includes both response digest and unique delivery ID; the body is stored only in the existing
content-addressed release artifact store. The dashboard route itself is not a supervisor task,
so this delivery receipt does not falsely complete an unrelated parent execution task.

MCP stdio remains an atomic JSON-RPC frame and is not chunked. The chunked HTTP mode is opt-in;
legacy clients retain atomic JSON with the existing 4 MB cap. A 16 MB total cap and 12 KB chunks
bound host memory and transfer units. If the client cannot durably store chunks or verify the final
digest, it must stop without posting completion.
