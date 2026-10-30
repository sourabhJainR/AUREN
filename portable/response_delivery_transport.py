"""Bounded, resumable delivery adapter for transports that support chunked replies.

The receiver call is deliberately synchronous: it provides backpressure and keeps
at most one chunk in flight. A transport must return an acknowledgement bound to
the exact response, index and chunk digest. A send with an ambiguous outcome is
never retried automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Mapping, Protocol

from portable.response_delivery import ResponseChunk, chunk_response, chunks_after
from portable.progress_journal import ProgressJournal


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
) -> DeliveryReceipt:
    """Deliver losslessly with one-chunk backpressure and durable ack checkpoints.

    This adapter is only for receivers whose protocol explicitly supports chunks.
    MCP stdio frames and other atomic protocols must retain their frame-level cap.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be non-empty")
    if not isinstance(text, str):
        raise TypeError("response text must be a string")
    if type(max_total_bytes) is not int or max_total_bytes < 0:
        raise ValueError("max_total_bytes must be a non-negative integer")
    total_bytes = len(text.encode("utf-8"))
    if total_bytes > max_total_bytes:
        raise DeliveryBlocked("response exceeds configured total byte budget; split the task or raise an approved budget")
    manifest, chunks = chunk_response(text, max_bytes=max_chunk_bytes)
    checkpoint = journal.response_delivery_checkpoint(task_id, manifest.response_id)
    acknowledged = checkpoint["acknowledged_index"]
    known_total = checkpoint["total_chunks"]
    if known_total is not None and known_total != manifest.total_chunks:
        raise DeliveryBlocked("stored acknowledgement manifest does not match response")
    if acknowledged > manifest.total_chunks:
        raise DeliveryBlocked("stored acknowledgement cursor exceeds response")
    started = time.monotonic()
    peak_in_flight = 0

    for chunk in chunks_after(chunks, acknowledged):
        if is_cancelled is not None and is_cancelled():
            raise DeliveryBlocked("delivery cancelled before next chunk")
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise DeliveryBlocked("delivery deadline reached before next chunk")
        if consume_shared_budget is not None and not consume_shared_budget(chunk.utf8_bytes):
            raise DeliveryBlocked("shared crew byte budget exhausted before next chunk")

        peak_in_flight = max(peak_in_flight, chunk.utf8_bytes)
        try:
            ack = receiver(chunk)
        except BaseException as exc:
            raise DeliveryOutcomeUnknown(
                "receiver outcome is unknown; query receiver status or use its idempotency key before retrying"
            ) from exc
        if not isinstance(ack, Mapping):
            raise DeliveryOutcomeUnknown("receiver returned no verifiable acknowledgement")
        expected = {
            "response_id": manifest.response_id,
            "index": chunk.index,
            "chunk_sha256": chunk.sha256,
        }
        if any(ack.get(key) != value for key, value in expected.items()):
            raise DeliveryOutcomeUnknown("receiver acknowledgement does not match the exact chunk")
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
        raise DeliveryBlocked("delivery is not fully acknowledged; parent task must remain nonterminal")
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
