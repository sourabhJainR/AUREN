"""Bounded, resumable delivery adapter for transports that support chunked replies.

The receiver call is deliberately synchronous: it provides backpressure and keeps
at most one chunk in flight. A transport must return an acknowledgement bound to
the exact response, index and chunk digest. A send with an ambiguous outcome is
never retried automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping, Protocol

from portable.response_delivery import ResponseChunk, chunk_response, chunks_after
from portable.progress_journal import ProgressJournal
from portable.response_delivery_metrics import record_delivery_metrics


class ChunkReceiver(Protocol):
    def __call__(self, chunk: ResponseChunk) -> Mapping[str, object]: ...


class DeliveryBlocked(RuntimeError):
    """Delivery cannot safely continue without changing policy or transport."""


class DeliveryOutcomeUnknown(DeliveryBlocked):
    """A send may have reached the receiver, but its acknowledgement is unknown."""


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    response_id: str
    total_chunks: int
    acknowledged_chunks: int
    total_bytes: int
    peak_in_flight_bytes: int
    resumed_from_chunk: int
    elapsed_seconds: float


def deliver_response(
    task_id: str,
    text: str,
    *,
    receiver: ChunkReceiver,
    journal: ProgressJournal,
    max_chunk_bytes: int = 12_000,
    max_total_bytes: int = 8_000_000,
    deadline_monotonic: float | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    consume_shared_budget: Callable[[int], bool] | None = None,
    execution_guard: Any | None = None,
) -> DeliveryReceipt:
    """Deliver losslessly with one-chunk backpressure and durable ack checkpoints.

    This adapter is only for receivers whose protocol explicitly supports chunks.
    task_id must identify an already-running supervisor task; this adapter never
    creates or revives task state. MCP stdio frames and other atomic protocols
    must retain their frame-level cap.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be non-empty")
    if not callable(receiver):
        raise TypeError("receiver must be callable and support the chunk acknowledgement contract")
    if not isinstance(text, str):
        raise TypeError("response text must be a string")
    if consume_shared_budget is not None and execution_guard is not None:
        raise ValueError("provide either the shared execution guard or a legacy budget callback, not both")
    if execution_guard is not None and not callable(getattr(execution_guard, "consume_delivery_bytes", None)):
        raise TypeError("execution_guard must expose consume_delivery_bytes")
    if type(max_total_bytes) is not int or max_total_bytes < 0:
        raise ValueError("max_total_bytes must be a non-negative integer")
    total_bytes = len(text.encode("utf-8"))
    if total_bytes > max_total_bytes:
        raise DeliveryBlocked("response exceeds configured total byte budget; split the task or raise an approved budget")
    manifest, chunks = chunk_response(text, max_bytes=max_chunk_bytes)
    delivery_stage = f"response-delivery:{manifest.response_id}"
    if any(
        row.get("task_id") == task_id
        and row.get("stage") == delivery_stage
        and row.get("event") == "response_chunk_outcome_unknown"
        for row in journal._events()
    ):
        raise DeliveryBlocked(
            "an earlier chunk has an unknown remote outcome; reconcile receiver status "
            "under a new operator-approved task before retrying"
        )
    checkpoint = journal.response_delivery_checkpoint(task_id, manifest.response_id)
    acknowledged = checkpoint["acknowledged_index"]
    known_total = checkpoint["total_chunks"]
    if known_total is not None and known_total != manifest.total_chunks:
        raise DeliveryBlocked("stored acknowledgement manifest does not match response")
    if acknowledged > manifest.total_chunks:
        raise DeliveryBlocked("stored acknowledgement cursor exceeds response")
    started = time.monotonic()
    peak_in_flight = 0
    receiver_wait_ms = 0.0

    def record_metrics(*, failure_reason: str | None = None, completion_digest: str | None = None) -> None:
        # Telemetry is advisory. It must never replace a delivery result,
        # acknowledgement, or typed unknown-outcome fence.
        try:
            record_delivery_metrics(
                journal, task_id, manifest.response_id,
                total_bytes=manifest.total_bytes, total_chunks=manifest.total_chunks,
                peak_in_flight_bytes=peak_in_flight,
                backpressure_wait_ms=receiver_wait_ms,
                resumed_from_chunk=acknowledged,
                failure_reason=failure_reason, completion_digest=completion_digest,
            )
        except Exception:
            return

    for chunk in chunks_after(chunks, acknowledged):
        if is_cancelled is not None and is_cancelled():
            record_metrics(failure_reason="cancelled")
            raise DeliveryBlocked("delivery cancelled before next chunk")
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            record_metrics(failure_reason="deadline_exceeded")
            raise DeliveryBlocked("delivery deadline reached before next chunk")
        if execution_guard is not None:
            execution_guard.check()
            operation_key = f"delivery:{task_id}:{manifest.response_id}:{chunk.index}:{chunk.sha256}"
            if not execution_guard.consume_delivery_bytes(chunk.utf8_bytes, operation_key=operation_key):
                record_metrics(failure_reason="shared_budget_exhausted")
                raise DeliveryBlocked("shared crew delivery byte budget exhausted or this chunk was already charged")
        elif consume_shared_budget is not None and not consume_shared_budget(chunk.utf8_bytes):
            record_metrics(failure_reason="shared_budget_exhausted")
            raise DeliveryBlocked("shared crew byte budget exhausted before next chunk")

        peak_in_flight = max(peak_in_flight, chunk.utf8_bytes)
        receiver_started = time.perf_counter()
        try:
            ack = receiver(chunk)
        except Exception as exc:
            receiver_wait_ms += (time.perf_counter() - receiver_started) * 1000.0
            # Persist the advisory category while the task is still writable;
            # then record the authoritative blocked/replay-fence transition.
            record_metrics(failure_reason="receiver_exception")
            journal.record(
                task_id, "response_chunk_outcome_unknown", state="blocked",
                stage=delivery_stage,
                message=f"receiver outcome unknown for chunk {chunk.index}; automatic replay disabled",
                evidence_ref=f"sha256:{manifest.sha256}",
            )
            raise DeliveryOutcomeUnknown(
                "receiver outcome is unknown; automatic replay is blocked until receiver status is reconciled"
            ) from exc
        receiver_wait_ms += (time.perf_counter() - receiver_started) * 1000.0
        if not isinstance(ack, Mapping):
            record_metrics(failure_reason="missing_ack")
            journal.record(
                task_id, "response_chunk_outcome_unknown", state="blocked",
                stage=delivery_stage,
                message=f"receiver returned no verifiable acknowledgement for chunk {chunk.index}",
                evidence_ref=f"sha256:{manifest.sha256}",
            )
            raise DeliveryOutcomeUnknown("receiver returned no verifiable acknowledgement; automatic replay is blocked")
        expected = {
            "response_id": manifest.response_id,
            "index": chunk.index,
            "chunk_sha256": chunk.sha256,
        }
        if any(ack.get(key) != value for key, value in expected.items()):
            record_metrics(failure_reason="ack_mismatch")
            journal.record(
                task_id, "response_chunk_outcome_unknown", state="blocked",
                stage=delivery_stage,
                message=f"receiver acknowledgement mismatch for chunk {chunk.index}; automatic replay disabled",
                evidence_ref=f"sha256:{manifest.sha256}",
            )
            raise DeliveryOutcomeUnknown("receiver acknowledgement mismatch; automatic replay is blocked")
        # Persist only after the receiver has confirmed this exact chunk.
        journal.record_response_chunk_ack(
            task_id,
            response_id=manifest.response_id,
            acknowledged_index=chunk.index,
            total_chunks=manifest.total_chunks,
            response_sha256=manifest.sha256,
        )

    final = journal.response_delivery_checkpoint(task_id, manifest.response_id)
    if final["acknowledged_index"] != manifest.total_chunks or final["response_sha256"] != manifest.sha256:
        record_metrics(failure_reason="ack_cursor_incomplete")
        raise DeliveryBlocked("delivery is not fully acknowledged; parent task must remain nonterminal")
    record_metrics(completion_digest=manifest.sha256)
    return DeliveryReceipt(
        response_id=manifest.response_id,
        total_chunks=manifest.total_chunks,
        acknowledged_chunks=final["acknowledged_index"],
        total_bytes=manifest.total_bytes,
        peak_in_flight_bytes=peak_in_flight,
        resumed_from_chunk=acknowledged,
        elapsed_seconds=max(0.0, time.monotonic() - started),
    )


__all__ = [
    "DeliveryBlocked", "DeliveryOutcomeUnknown", "DeliveryReceipt",
    "deliver_response",
]
