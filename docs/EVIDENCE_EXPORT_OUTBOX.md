# Evidence Export Outbox Contract

## Delivery model

Local engineering events and their outbox rows are persisted before remote delivery. Each event keeps one stable `event_id`; the HTTPS sink sends that ID as the idempotency key. Export is **at-least-once**, not exactly-once. If the remote sink accepts an event and the worker dies before the local acknowledgement commits, the same event can be sent again after its claim lease expires. A configured sink must deduplicate by the stable event ID.

## Concurrent workers and crash recovery

- `flush_pending(limit=100, lease_seconds=300)` claims at most `limit` rows in one short SQLite write transaction. Network calls happen after the transaction commits.
- `claim_token` identifies the worker that owns a pending row. A second live worker cannot claim that row while its lease is fresh.
- A claim older than `lease_seconds` can be replaced by a new claim. Lease values are bounded to 1–86400 seconds.
- `pending_exports()` counts every outbox row that is not confirmed as sent, including active or stale claims. Unknown delivery states remain visible instead of being reported as complete.
- A delivery error clears only the matching claim, stores the exception type, and leaves the event pending. Attempts are counted when a claim is acquired.
- The schema upgrade adds `claim_token` with an empty default. Existing pending rows and event IDs are retained.

## Operator checks

1. Inspect `pending_exports()`, `attempts`, `last_error`, `delivery_state`, and claim age before recovery.
2. Let a stale claim expire or stop all exporters before performing a controlled recovery. Do not delete an outbox row to clear a warning.
3. Verify the remote collector's deduplication contract for `Idempotency-Key` and confirm the retained event ID, not only the local `sent` state.
4. If the remote outcome is unknown, preserve the row and evidence database; retry only with the same event ID.

## Rollback

Pause every evidence exporter and back up the evidence database before reverting code. The added column is backward-compatible, and old code can still read pending rows. Keep all event IDs and outbox rows. Never reset a confirmed `sent` row to pending. If manual claim recovery is necessary, clear only stale non-empty claim tokens after all senders have stopped, preserve attempts and event payloads, and record the operator action. A code rollback does not undo a remote delivery.
