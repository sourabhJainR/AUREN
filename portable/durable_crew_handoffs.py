"""Durable, replay-safe cross-crew handoff ledger.

The ledger stores only bounded JSON-compatible handoff records in an atomic
checkpoint file. Graph/supervisor remains the execution authority. A claimed
handoff may be delivered at least once after a crash; consumers must use the
handoff_id as an idempotency key for any external effect. Completion requires
an independent review receipt and is never inferred from a crew's own status.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, Mapping


SCHEMA = "auren-durable-crew-handoffs-v1"
_STATES = {"queued", "in_flight", "completed", "rejected"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class DurableHandoffLedger:
    """Atomic checkpointed handoff queue with stable IDs and fail-closed review."""

    def __init__(self, path: str | Path, *, max_records: int = 10000) -> None:
        if type(max_records) is not int or not 1 <= max_records <= 1_000_000:
            raise ValueError("max_records must be between 1 and 1000000")
        self.path = Path(path)
        self.max_records = max_records
        self._lock = RLock()
        self._records: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            self._load()

    def _payload(self) -> dict[str, Any]:
        records = [self._records[key] for key in sorted(self._records)]
        payload = {"schema_version": SCHEMA, "records": records}
        payload["digest"] = _digest(payload)
        return payload

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("handoff checkpoint cannot be read") from exc
        if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA:
            raise ValueError("unsupported handoff checkpoint schema")
        supplied = payload.get("digest")
        unsigned = {"schema_version": payload.get("schema_version"), "records": payload.get("records")}
        if not isinstance(supplied, str) or supplied != _digest(unsigned):
            raise ValueError("handoff checkpoint digest mismatch")
        records = payload.get("records")
        if not isinstance(records, list) or len(records) > self.max_records:
            raise ValueError("invalid or oversized handoff record list")
        loaded: dict[str, dict[str, Any]] = {}
        for record in records:
            self._validate_record(record)
            hid = record["handoff_id"]
            if hid in loaded:
                raise ValueError("duplicate handoff_id in checkpoint")
            loaded[hid] = dict(record)
        self._records = loaded

    @staticmethod
    def _validate_record(record: Any) -> None:
        if not isinstance(record, Mapping):
            raise ValueError("handoff record must be an object")
        required = {"handoff_id", "source_crew", "target_crew", "task", "payload", "state", "attempts", "review_receipt"}
        if set(record) != required:
            raise ValueError("handoff record schema mismatch")
        for key in ("handoff_id", "source_crew", "target_crew", "task"):
            if not isinstance(record[key], str) or not record[key].strip():
                raise ValueError(f"{key} must be non-empty")
        if record["source_crew"] == record["target_crew"]:
            raise ValueError("cross-crew handoff requires distinct source and target")
        if record["state"] not in _STATES:
            raise ValueError("invalid handoff state")
        if type(record["attempts"]) is not int or record["attempts"] < 0:
            raise ValueError("attempts must be a non-negative integer")
        if record["state"] == "completed":
            receipt = record["review_receipt"]
            if not isinstance(receipt, Mapping) or receipt.get("independent") is not True or not receipt.get("reviewer_id"):
                raise ValueError("completed handoff requires an independent review receipt")
        _canonical(record["payload"])

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._payload()
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                # Directory fsync is not supported on every platform.
                pass
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def enqueue(self, *, handoff_id: str, source_crew: str, target_crew: str, task: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("handoff payload must be a mapping")
        _canonical(payload)
        with self._lock:
            existing = self._records.get(handoff_id)
            if existing is not None:
                expected = (source_crew, target_crew, task, dict(payload))
                actual = (existing["source_crew"], existing["target_crew"], existing["task"], existing["payload"])
                if actual != expected:
                    raise ValueError("handoff_id reused with conflicting content")
                return dict(existing)
            if len(self._records) >= self.max_records:
                raise RuntimeError("handoff queue capacity reached")
            record = {
                "handoff_id": handoff_id, "source_crew": source_crew,
                "target_crew": target_crew, "task": task, "payload": dict(payload),
                "state": "queued", "attempts": 0, "review_receipt": None,
            }
            self._validate_record(record)
            self._records[handoff_id] = record
            self._save()
            return dict(record)

    def claim(self, handoff_id: str) -> dict[str, Any]:
        """Claim queued or interrupted work; duplicate delivery is explicit."""
        with self._lock:
            record = self._records.get(handoff_id)
            if record is None:
                raise KeyError(handoff_id)
            if record["state"] == "completed" or record["state"] == "rejected":
                raise ValueError("terminal handoff cannot be claimed")
            record["state"] = "in_flight"
            record["attempts"] += 1
            self._save()
            return dict(record)

    def complete(self, handoff_id: str, review_receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Complete only after an independent reviewer receipt is supplied."""
        if not isinstance(review_receipt, Mapping) or review_receipt.get("independent") is not True:
            raise PermissionError("independent review is required to complete a handoff")
        reviewer_id = review_receipt.get("reviewer_id")
        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            raise ValueError("reviewer_id is required")
        receipt = dict(review_receipt)
        _canonical(receipt)
        with self._lock:
            record = self._records.get(handoff_id)
            if record is None:
                raise KeyError(handoff_id)
            if record["state"] != "in_flight":
                raise ValueError("only an in-flight handoff can complete")
            record["state"] = "completed"
            record["review_receipt"] = receipt
            self._save()
            return dict(record)

    def reject(self, handoff_id: str, *, reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("rejection reason is required")
        with self._lock:
            record = self._records.get(handoff_id)
            if record is None:
                raise KeyError(handoff_id)
            if record["state"] == "completed":
                raise ValueError("completed handoff cannot be rejected")
            record["state"] = "rejected"
            record["review_receipt"] = {"reason": reason, "independent": False}
            self._save()
            return dict(record)

    def get(self, handoff_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(handoff_id)
            return dict(record) if record else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._payload()


__all__ = ["DurableHandoffLedger", "SCHEMA"]
