"""Append-only receiver-side archive for AUREN engineering evidence.

The HTTP service in scripts/evidence_archive_server.py exposes this store behind
an operator-managed TLS reverse proxy. This module never stores raw task payloads,
sealed corpus cases, or oracle answers.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse
import sqlite3
from typing import Any, Mapping


_EVENT_KEYS = {
    "event_id", "event_type", "source_type", "occurred_at", "work_unit_id",
    "task_id", "iteration", "status", "repository", "head_sha", "artifact_digest",
    "evidence_uri", "metadata", "previous_digest", "event_digest", "signer_id", "signature",
}
_EVENT_TYPES = {
    "task_submitted", "task_attempt_started", "regression_passed", "regression_failed",
    "recovery_selected", "task_completed", "task_failed", "external_evidence_observed",
    "rollback_started", "rollback_completed", "rollback_failed",
}
_SOURCE_TYPES = {
    "auren_runtime", "ci", "external_evaluator", "oracle_custody",
    "sealed_corpus_custody", "isolation_audit", "operator",
}
_SIGNED_EXTERNAL_SOURCES = {
    "external_evaluator", "oracle_custody", "sealed_corpus_custody",
    "isolation_audit", "operator",
}
_SOURCE_EVIDENCE_TYPES = {
    "external_evaluator": {"independent_evaluator_receipt", "external_evaluator_receipt", "arena_receipt"},
    "oracle_custody": {"oracle_custody"},
    "sealed_corpus_custody": {"sealed_corpus_custody"},
    "isolation_audit": {"isolation_audit"},
    "operator": {"approval", "promotion_approval", "operator_approval", "rollback_rehearsal", "rollback_test"},
}
_HEX64 = set("0123456789abcdef")
_HEX40 = set("0123456789abcdef")
_ALLOWED_METADATA = {
    "phase", "handler", "failure_class", "hypothesis_digest", "test_suite",
    "test_count", "passed_count", "failed_count", "duration_ms", "retry_action",
    "external_system", "run_id", "evaluator_id", "oracle_id", "corpus_digest",
    "isolation_profile", "signature_key_id", "rollback_plan_digest",
    "previous_head_sha", "result_category", "receipt_digest", "operator_id",
    "target_id", "last_known_good_commit", "recovered_head_sha",
}
_SECRET_MARKERS = ("password", "secret", "authorization", "oracle_answer", "oracle answer", "private_key", "private key", "api_key", "api-key", "token=", "prompt=", "bearer ")


class ArchiveConflict(ValueError):
    """An event ID was reused with different immutable content."""


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_json(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


class EvidenceArchive:
    """Persist authenticated-ingress events without permitting event mutation."""

    def __init__(self, database_path: str | Path, *, receipt_verifier: Any = None) -> None:
        self.path = Path(database_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.receipt_verifier = receipt_verifier
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS archived_events(
                event_id TEXT PRIMARY KEY,
                event_digest TEXT NOT NULL,
                event_json TEXT NOT NULL,
                authenticated_principal TEXT NOT NULL,
                receipt_digest TEXT NOT NULL,
                receipt_signature_verified INTEGER NOT NULL,
                received_at TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS archived_events_head ON archived_events(json_extract(event_json, '$.repository'), json_extract(event_json, '$.head_sha'))")

    def _validate(self, event: Mapping[str, Any]) -> tuple[dict[str, Any], bool, str]:
        if not isinstance(event, Mapping):
            raise ValueError("event must be a JSON object")
        allowed = _EVENT_KEYS | {"signed_receipt"}
        if set(event) - allowed or _EVENT_KEYS - set(event):
            raise ValueError("event schema contains missing or unknown fields")
        if event["event_type"] not in _EVENT_TYPES or event["source_type"] not in _SOURCE_TYPES:
            raise ValueError("event type or source type is unsupported")
        if event["source_type"] in _SIGNED_EXTERNAL_SOURCES and "signed_receipt" not in event:
            raise ValueError("external evaluator, custody, isolation, and operator evidence requires a signed receipt")
        if "signed_receipt" in event and self.receipt_verifier is None:
            raise ValueError("a trusted receipt verifier is required before signed evidence can be archived")
        if not isinstance(event["metadata"], Mapping):
            raise ValueError("event metadata must be an object")
        if set(event["metadata"]) - _ALLOWED_METADATA:
            raise ValueError("event metadata contains unknown or unsafe fields")
        for key, value in event["metadata"].items():
            if not isinstance(value, (str, bool, int, float, type(None))):
                raise ValueError(f"event metadata field {key} must be a scalar")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"event metadata field {key} must be finite")
            if isinstance(value, str) and (
                len(value) > 512 or any(ord(ch) < 32 for ch in value)
                or any(marker in value.lower() for marker in _SECRET_MARKERS)
            ):
                raise ValueError(f"event metadata field {key} appears unsafe")
            if key.endswith("_digest") and (not isinstance(value, str) or len(value) != 64 or any(ch not in _HEX64 for ch in value)):
                raise ValueError(f"event metadata field {key} must be a SHA-256 digest")
            if key in {"test_count", "passed_count", "failed_count", "duration_ms"} and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"event metadata field {key} must be a non-negative integer")
        if isinstance(event["iteration"], bool) or not isinstance(event["iteration"], int) or event["iteration"] < 0:
            raise ValueError("event iteration must be a non-negative integer")
        if event["head_sha"] and (
            not isinstance(event["head_sha"], str) or len(event["head_sha"]) != 40
            or any(ch not in _HEX40 for ch in event["head_sha"])
        ):
            raise ValueError("event head_sha must be a full lowercase commit SHA")
        for key in ("event_digest", "previous_digest"):
            value = event[key]
            if not isinstance(value, str) or len(value) != 64 or any(ch not in _HEX64 for ch in value):
                raise ValueError(f"{key} must be a lowercase SHA-256 digest")
        unsigned = {key: event[key] for key in (
            "event_id", "event_type", "source_type", "occurred_at", "work_unit_id",
            "task_id", "iteration", "status", "repository", "head_sha", "artifact_digest",
            "evidence_uri", "metadata", "previous_digest",
        )}
        if sha256_json(unsigned) != event["event_digest"]:
            raise ValueError("event digest verification failed")

        receipt_verified = False
        receipt_digest = ""
        if "signed_receipt" in event:
            envelope = event["signed_receipt"]
            if not isinstance(envelope, Mapping) or set(envelope) != {"payload", "signer_id", "signature"}:
                raise ValueError("signed receipt envelope is malformed")
            payload = envelope["payload"]
            if not isinstance(payload, Mapping):
                raise ValueError("signed receipt payload must be an object")
            required = {
                "evidence_id", "repository", "head_sha", "evidence_type", "status",
                "source_uri", "payload_digest", "source_type", "issued_at", "run_id",
            }
            allowed_payload = required | {
                "evaluator_id", "oracle_id", "runtime_id", "corpus_digest",
                "isolation_profile", "external_system", "operator_id", "target_id",
                "rollback_plan_digest", "last_known_good_commit", "recovered_head_sha",
            }
            if required - set(payload) or set(payload) - allowed_payload:
                raise ValueError("signed receipt contains missing or unknown fields")
            expected_evidence_types = _SOURCE_EVIDENCE_TYPES.get(str(event["source_type"]))
            if expected_evidence_types is not None and payload.get("evidence_type") not in expected_evidence_types:
                raise ValueError("signed evidence type does not match its trusted source role")
            if (
                payload.get("repository") != event["repository"]
                or not isinstance(payload.get("head_sha"), str)
                or len(payload["head_sha"]) != 40
                or any(ch not in _HEX40 for ch in payload["head_sha"])
                or not isinstance(payload.get("payload_digest"), str)
                or len(payload["payload_digest"]) != 64
                or any(ch not in _HEX64 for ch in payload["payload_digest"])
                or payload.get("status") not in {"passed", "failed", "skipped", "unknown", "verified"}
                or payload.get("source_type") not in _SOURCE_TYPES - {"auren_runtime"}
            ):
                raise ValueError("signed receipt contains invalid repository, head, digest, status, or source")
            try:
                issued = datetime.fromisoformat(str(payload["issued_at"]).replace("Z", "+00:00"))
                if issued.tzinfo is None or issued.utcoffset() is None:
                    raise ValueError("receipt issued_at must include a timezone")
                age = (datetime.now(timezone.utc) - issued.astimezone(timezone.utc)).total_seconds()
            except (TypeError, OverflowError) as exc:
                raise ValueError("signed receipt issued_at is invalid") from exc
            max_age = 90 * 24 * 3600 if payload["evidence_type"] == "rollback_rehearsal" else 7 * 24 * 3600
            if age > max_age or age < -300:
                raise ValueError("signed receipt is stale or issued too far in the future")
            if payload["evidence_type"] == "rollback_rehearsal":
                if payload["source_type"] != "operator":
                    raise ValueError("rollback rehearsal receipt must come from an operator")
                required_rollback = {
                    "operator_id", "target_id", "rollback_plan_digest",
                    "last_known_good_commit", "recovered_head_sha",
                }
                if required_rollback - set(payload):
                    raise ValueError("rollback receipt is missing operator rehearsal fields")
                if payload["status"] in {"passed", "verified"} and payload["recovered_head_sha"] != payload["last_known_good_commit"]:
                    raise ValueError("successful rollback receipt must recover to last-known-good")
            source_uri = str(payload.get("source_uri", ""))
            if not source_uri.startswith(("https://", "file:", "urn:")):
                raise ValueError("signed receipt source_uri must use https, file, or urn")
            if source_uri.startswith("https://"):
                parsed_uri = urlparse(source_uri)
                if parsed_uri.username or parsed_uri.password or parsed_uri.query:
                    raise ValueError("signed receipt URI must not embed credentials or query-string secrets")
            receipt_digest = sha256_json(envelope)
            metadata = event["metadata"]
            if (
                metadata.get("receipt_digest") != receipt_digest
                or payload.get("repository") != event["repository"]
                or payload.get("head_sha") != event["head_sha"]
                or payload.get("source_type") != event["source_type"]
                or payload.get("status") != event["status"]
                or payload.get("source_uri") != event["evidence_uri"]
                or payload.get("payload_digest") != event["artifact_digest"]
                or envelope["signer_id"] in {
                    payload.get("evaluator_id"), payload.get("oracle_id"), payload.get("runtime_id")
                }
            ):
                raise ValueError("signed receipt does not match the archived event")
            if event["source_type"] in {"oracle_custody", "sealed_corpus_custody", "isolation_audit", "operator"}:
                if not isinstance(payload.get("operator_id"), str) or not payload["operator_id"].strip():
                    raise ValueError("custody, audit, and operator receipts must name the responsible operator")
            if self.receipt_verifier is not None:
                message = canonical_json(payload)
                if hasattr(self.receipt_verifier, "verify_detached"):
                    receipt_verified = self.receipt_verifier.verify_detached(
                        envelope["signer_id"], envelope["signature"], message
                    ) is True
                else:
                    receipt_verified = self.receipt_verifier(
                        envelope["signer_id"], envelope["signature"], message
                    ) is True
                if not receipt_verified:
                    raise ValueError("external signed receipt verification failed")
        return dict(event), receipt_verified, receipt_digest

    def ingest(self, event: Mapping[str, Any], *, authenticated_principal: str) -> dict[str, Any]:
        if not isinstance(authenticated_principal, str) or not authenticated_principal.strip():
            raise ValueError("authenticated_principal is required")
        clean, receipt_verified, receipt_digest = self._validate(event)
        event_id = clean["event_id"]
        event_json = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT event_digest,event_json,authenticated_principal,receipt_signature_verified FROM archived_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if prior:
                if prior[0] != clean["event_digest"] or prior[1] != event_json:
                    raise ArchiveConflict("event_id already exists with different immutable content")
                return {
                    "event_id": event_id,
                    "event_digest": prior[0],
                    "status": "already_retained",
                    "receipt_signature_verified": bool(prior[3]),
                }
            db.execute(
                "INSERT INTO archived_events VALUES(?,?,?,?,?,?,?)",
                (
                    event_id, clean["event_digest"], event_json, authenticated_principal,
                    receipt_digest, int(receipt_verified), datetime.now(timezone.utc).isoformat(),
                ),
            )
        return {
            "event_id": event_id,
            "event_digest": clean["event_digest"],
            "status": "retained",
            "receipt_signature_verified": receipt_verified,
        }

    def get(self, event_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            row = db.execute(
                "SELECT event_digest,event_json,authenticated_principal,receipt_digest,receipt_signature_verified,received_at FROM archived_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "event_digest": row[0],
            "event": json.loads(row[1]),
            "authenticated_principal": row[2],
            "receipt_digest": row[3],
            "receipt_signature_verified": bool(row[4]),
            "received_at": row[5],
        }


__all__ = ["EvidenceArchive", "ArchiveConflict", "canonical_json", "sha256_json"]
