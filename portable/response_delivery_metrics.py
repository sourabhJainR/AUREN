"""Privacy-safe, durable response-delivery measurement events."""
from __future__ import annotations

import json
import math
import time
from typing import Any

from .progress_journal import ProgressJournal


SCHEMA_VERSION = "auren-response-delivery-metrics-v1"


def record_delivery_metrics(
    journal: ProgressJournal,
    task_id: str,
    response_id: str,
    *,
    total_bytes: int,
    total_chunks: int,
    peak_in_flight_bytes: int,
    backpressure_wait_ms: float,
    resumed_from_chunk: int,
    failure_reason: str | None = None,
    completion_digest: str | None = None,
) -> None:
    """Append counters and safe failure categories; never record response content."""
    if not isinstance(response_id, str) or len(response_id) != 64 or any(
        ch not in "0123456789abcdef" for ch in response_id
    ):
        raise ValueError("response_id must be a lowercase SHA-256 digest")
    integer_values = {
        "total_bytes": total_bytes,
        "total_chunks": total_chunks,
        "peak_in_flight_bytes": peak_in_flight_bytes,
        "resumed_from_chunk": resumed_from_chunk,
    }
    for name, value in integer_values.items():
        if type(value) is not int:
            raise TypeError(f"{name} must be an integer")
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
    if resumed_from_chunk > total_chunks:
        raise ValueError("resumed_from_chunk cannot exceed total_chunks")
    if (
        isinstance(backpressure_wait_ms, bool)
        or not isinstance(backpressure_wait_ms, (int, float))
        or not math.isfinite(backpressure_wait_ms)
        or backpressure_wait_ms < 0
    ):
        raise ValueError("backpressure_wait_ms must be a finite non-negative number")
    values = {
        **integer_values,
        "backpressure_wait_ms": round(float(backpressure_wait_ms), 3),
    }
    if completion_digest is not None and completion_digest != response_id:
        raise ValueError("completion digest must match the response identity")
    if failure_reason is not None and (
        not isinstance(failure_reason, str)
        or not failure_reason
        or len(failure_reason) > 80
        or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for ch in failure_reason)
    ):
        raise ValueError("failure_reason must be a short safe category")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "response_id": response_id,
        **values,
        "failure_reason": failure_reason,
        "completion_digest": completion_digest,
    }
    snapshot = journal.snapshot(task_id)
    state = snapshot.get("state", "running")
    if state not in {"queued", "running", "blocked", "recovering", "completed", "failed", "cancelled"}:
        state = "running"
    journal.record(
        task_id,
        "response_delivery_metrics",
        state=state,
        stage=f"response-delivery:{response_id}",
        message=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        evidence_ref=("sha256:" + completion_digest) if completion_digest else None,
    )


def record_chunk_offer(
    journal: ProgressJournal, task_id: str, response_id: str, *,
    chunk_index: int, chunk_sha256: str, chunk_bytes: int,
    total_bytes: int, total_chunks: int,
) -> None:
    """Record a bounded offer timestamp to measure pull/ack backpressure."""
    if type(chunk_index) is not int or chunk_index < 1:
        raise ValueError("chunk_index must be positive")
    if type(chunk_bytes) is not int or chunk_bytes < 0:
        raise ValueError("chunk_bytes must be non-negative")
    if type(total_bytes) is not int or total_bytes < 0:
        raise ValueError("total_bytes must be non-negative")
    if type(total_chunks) is not int or total_chunks < chunk_index:
        raise ValueError("total_chunks must include the offered chunk")
    if len(chunk_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in chunk_sha256):
        raise ValueError("chunk_sha256 must be a lowercase digest")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "response_id": response_id,
        "chunk_index": chunk_index,
        "chunk_sha256": chunk_sha256,
        "chunk_bytes": chunk_bytes,
        "total_bytes": total_bytes,
        "total_chunks": total_chunks,
        "offered_at_unix": time.time(),
    }
    state = journal.snapshot(task_id).get("state", "running")
    if state not in {"queued", "running", "blocked", "recovering", "completed", "failed", "cancelled"}:
        state = "running"
    journal.record(
        task_id, "response_chunk_offered", state=state,
        stage=f"response-delivery:{response_id}",
        message=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        evidence_ref="sha256:" + chunk_sha256,
    )


def record_chunk_ack_measurement(
    journal: ProgressJournal, task_id: str, response_id: str, *,
    chunk_index: int, chunk_sha256: str, chunk_bytes: int,
) -> float:
    """Persist one idempotent offer-to-ack wait measurement for a chunk."""
    events = journal._events()
    for row in events:
        if row.get("task_id") != task_id or row.get("event") != "response_delivery_chunk_measurement":
            continue
        try:
            prior = json.loads(str(row.get("message", "")))
        except (TypeError, ValueError):
            continue
        if (
            isinstance(prior, dict) and prior.get("response_id") == response_id
            and prior.get("chunk_index") == chunk_index
            and prior.get("chunk_sha256") == chunk_sha256
        ):
            return float(prior.get("backpressure_wait_ms", 0.0))
    offers = []
    for row in events:
        if row.get("task_id") != task_id or row.get("event") != "response_chunk_offered":
            continue
        try:
            payload = json.loads(str(row.get("message", "")))
        except (TypeError, ValueError):
            continue
        if (
            isinstance(payload, dict) and payload.get("schema_version") == SCHEMA_VERSION
            and payload.get("response_id") == response_id
            and payload.get("chunk_index") == chunk_index
            and payload.get("chunk_sha256") == chunk_sha256
        ):
            offered_at = payload.get("offered_at_unix")
            if isinstance(offered_at, (int, float)) and math.isfinite(offered_at):
                offers.append(float(offered_at))
    if not offers:
        wait_ms = 0.0
    else:
        wait_ms = max(0.0, (time.time() - min(offers)) * 1000.0)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "response_id": response_id,
        "chunk_index": chunk_index,
        "chunk_sha256": chunk_sha256,
        "chunk_bytes": chunk_bytes,
        "backpressure_wait_ms": round(wait_ms, 3),
    }
    state = journal.snapshot(task_id).get("state", "running")
    if state not in {"queued", "running", "blocked", "recovering", "completed", "failed", "cancelled"}:
        state = "running"
    journal.record(
        task_id, "response_delivery_chunk_measurement", state=state,
        stage=f"response-delivery:{response_id}",
        message=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        evidence_ref="sha256:" + chunk_sha256,
    )
    return round(wait_ms, 3)


def record_delivery_failure(
    journal: ProgressJournal, task_id: str, response_id: str, reason: str,
) -> None:
    """Record only a safe failure category, never exception text or payloads."""
    if not isinstance(reason, str) or not reason or len(reason) > 80 or any(
        ch not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for ch in reason
    ):
        raise ValueError("failure reason must be a short safe category")
    state = journal.snapshot(task_id).get("state", "running")
    if state not in {"queued", "running", "blocked", "recovering", "completed", "failed", "cancelled"}:
        state = "running"
    payload = {"schema_version": SCHEMA_VERSION, "response_id": response_id, "failure_reason": reason}
    journal.record(
        task_id, "response_delivery_failure", state=state,
        stage=f"response-delivery:{response_id}",
        message=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def aggregate_delivery_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the latest size per delivery and all observed wait/failure evidence."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    failure_reasons: set[str] = set()
    completion_digests: set[str] = set()
    total_wait = 0.0
    peak_in_flight = 0
    resume_count = 0
    for row in events:
        if not isinstance(row, dict) or row.get("event") != "response_delivery_metrics":
            continue
        try:
            payload = json.loads(str(row.get("message", "")))
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            continue
        task_id = row.get("task_id")
        response_id = payload.get("response_id")
        if not isinstance(task_id, str) or not isinstance(response_id, str):
            continue
        try:
            total_bytes = payload.get("total_bytes")
            total_chunks = payload.get("total_chunks")
            wait_ms = float(payload.get("backpressure_wait_ms", 0.0))
            peak_bytes = payload.get("peak_in_flight_bytes")
            resumed = payload.get("resumed_from_chunk")
            if (
                type(total_bytes) is not int or total_bytes < 0
                or type(total_chunks) is not int or total_chunks < 0
                or type(peak_bytes) is not int or peak_bytes < 0
                or type(resumed) is not int or not 0 <= resumed <= total_chunks
                or not math.isfinite(wait_ms) or wait_ms < 0
            ):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        latest[(task_id, response_id)] = payload
        total_wait += wait_ms
        peak_in_flight = max(peak_in_flight, peak_bytes)
        if resumed > 0:
            resume_count += 1
        failure = payload.get("failure_reason")
        if isinstance(failure, str) and 0 < len(failure) <= 80 and all(
            ch in "abcdefghijklmnopqrstuvwxyz0123456789_-" for ch in failure
        ):
            failure_reasons.add(failure)
        digest = payload.get("completion_digest")
        if isinstance(digest, str) and len(digest) == 64 and all(
            ch in "0123456789abcdef" for ch in digest
        ) and digest == response_id:
            completion_digests.add(digest)
    return {
        "measured_deliveries": len(latest),
        "total_bytes": sum(int(row.get("total_bytes", 0)) for row in latest.values()),
        "total_chunks": sum(int(row.get("total_chunks", 0)) for row in latest.values()),
        "peak_in_flight_bytes": peak_in_flight,
        "backpressure_wait_ms": round(total_wait, 3),
        "resume_count": resume_count,
        "failure_reasons": sorted(failure_reasons),
        "completion_digests": sorted(completion_digests),
    }



__all__ = ["SCHEMA_VERSION", "record_delivery_metrics", "aggregate_delivery_metrics"]
