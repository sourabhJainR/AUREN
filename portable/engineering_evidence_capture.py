"""Automatic, privacy-minimizing engineering evidence capture.

Capture local lifecycle/iteration metadata without a per-task prompt. Raw prompts,
payloads, source contents, credentials, oracle answers, and sealed corpus data are
never accepted by the event API. Remote export is only enabled by an operator-
configured HTTPS sink; independent claims require verified external attestations.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse
from uuid import uuid4

from .canonical_evidence_ledger import CanonicalEvidenceLedger
from .guarded_network import guarded_https_request


_EVENT_TYPES = frozenset({
    "task_submitted", "task_attempt_started", "regression_passed",
    "regression_failed", "recovery_selected", "task_completed", "task_failed",
    "external_evidence_observed", "rollback_started", "rollback_completed",
    "rollback_failed", "crew_handoff_checkpoint_persisted",
    "task_quota_wait", "task_quota_blocked", "engineering_plan_task_dispatched",
    "engineering_plan_dispatch_cancelled",
    "provider_operation_dispatching", "provider_operation_completed",
    "provider_operation_reconcile_required", "provider_operation_blocked",
    "approval_waiting", "approval_recovered_after_restart", "approval_resume_ready",
    "guard_dispatch_decision",
})
_SOURCE_TYPES = frozenset({
    "auren_runtime", "ci", "external_evaluator", "oracle_custody",
    "sealed_corpus_custody", "isolation_audit", "operator",
})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

_OUTBOX_CLAIM_LEASE_SECONDS = 120


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text(value: Any, field: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{field} must be a non-empty string of at most {limit} characters")
    return value.strip()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class EvidenceCapture:
    """Append-only hash-chained local journal with optional configured export.

    The journal is a tamper-evident index, not immutable storage against an OS
    administrator who can rewrite the database. Production deployments should
    export signed records to independently controlled retention/WORM storage.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        sink: Callable[[Mapping[str, Any]], None] | None = None,
        signer: Callable[[bytes], Mapping[str, str]] | None = None,
        evidence_ledger: CanonicalEvidenceLedger | None = None,
    ) -> None:
        self.path = Path(database_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sink = sink
        self.signer = signer
        self.evidence_ledger = evidence_ledger
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS engineering_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                source_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                work_unit_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                iteration INTEGER NOT NULL,
                status TEXT NOT NULL,
                repository TEXT NOT NULL,
                head_sha TEXT NOT NULL,
                artifact_digest TEXT NOT NULL,
                evidence_uri TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                previous_digest TEXT NOT NULL,
                event_digest TEXT NOT NULL,
                signer_id TEXT NOT NULL,
                signature TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS external_signed_receipts(
                receipt_digest TEXT PRIMARY KEY,
                evidence_id TEXT NOT NULL,
                signer_id TEXT NOT NULL,
                envelope_json TEXT NOT NULL,
                retained_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS engineering_event_outbox(
                event_id TEXT PRIMARY KEY,
                event_json TEXT NOT NULL,
                delivery_state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                claim_token TEXT NOT NULL DEFAULT '',
                claimed_at TEXT NOT NULL DEFAULT '',
                lease_expires_at TEXT NOT NULL DEFAULT ''
            )""")
            # Additive migration keeps existing pending rows and signed evidence intact.
            columns = {row[1] for row in db.execute("PRAGMA table_info(engineering_event_outbox)")}
            for column in ("claim_token", "claimed_at", "lease_expires_at"):
                if column not in columns:
                    db.execute(
                        f"ALTER TABLE engineering_event_outbox ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )

    @classmethod
    def from_environment(
        cls, database_path: str | Path, *,
        evidence_ledger: CanonicalEvidenceLedger | None = None,
        signer: Callable[[bytes], Mapping[str, str]] | None = None,
    ) -> "EvidenceCapture":
        """Enable automatic HTTPS export only when an operator configured a sink.

        No remote network access occurs merely because this class is imported.
        A signer can be supplied by a KMS/HSM adapter by the hosting runtime.
        """
        url = os.environ.get("AUREN_EVIDENCE_SINK_URL", "").strip()
        token = os.environ.get("AUREN_EVIDENCE_SINK_TOKEN", "")
        allowed_cidrs = tuple(
            item.strip() for item in os.environ.get("AUREN_EVIDENCE_SINK_ALLOWED_CIDRS", "").split(",")
            if item.strip()
        )
        sink = HttpsEvidenceSink(url, token, allowed_cidrs=allowed_cidrs) if url else None
        return cls(database_path, sink=sink, signer=signer, evidence_ledger=evidence_ledger)

    def record(
        self,
        event_type: str,
        *,
        work_unit_id: str,
        task_id: str,
        iteration: int = 0,
        status: str,
        repository: str = "",
        head_sha: str = "",
        artifact_digest: str = "",
        evidence_uri: str = "",
        source_type: str = "auren_runtime",
        metadata: Mapping[str, Any] | None = None,
        event_id: str | None = None,
        _defer_export: bool = False,
    ) -> dict[str, Any]:
        if event_type not in _EVENT_TYPES:
            raise ValueError("unsupported engineering event type")
        if source_type not in _SOURCE_TYPES:
            raise ValueError("unsupported evidence source type")
        if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 0:
            raise ValueError("iteration must be a non-negative integer")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be a mapping of non-sensitive structured values")
        # Allowlist prevents raw task payloads, prompts, test output, secrets, and
        # oracle labels from being accidentally serialized into the evidence store.
        allowed_metadata = {
            "phase", "handler", "failure_class", "hypothesis_digest",
            "test_suite", "test_count", "passed_count", "failed_count",
            "duration_ms", "retry_action", "external_system", "run_id",
            "evaluator_id", "oracle_id", "corpus_digest", "isolation_profile",
            "signature_key_id", "rollback_plan_digest",
            "previous_head_sha", "result_category", "receipt_digest", "operator_id",
            "target_id", "last_known_good_commit", "recovered_head_sha",
            "checkpoint_digest", "crew_plan_digest", "retry_budget_digest",
            "plan_digest", "packet_digest", "dispatch_key_digest", "binding_digest", "run_revision",
            "provider_resume_digest", "intent_digest", "operation_id", "provider", "operation_state", "approval_id",
            "workflow_profile_version", "workflow_profile_digest", "guard_policy_digest",
            "guard_policy_status", "guard_action", "guard_decision", "guard_rule_ids",
            "workflow_delivery_mode", "workflow_execution_mode", "workflow_approval_mode",
            "workflow_review_mode", "workflow_recovery_mode",
        }
        if "signature_verified" in metadata:
            raise ValueError("signature_verified is derived from the verified receipt store, not caller metadata")
        if set(metadata) - allowed_metadata:
            raise ValueError("metadata contains fields outside the safe evidence allowlist")
        safe_metadata: dict[str, Any] = {}
        for key, value in metadata.items():
            if key.endswith("_digest"):
                if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
                    raise ValueError(f"{key} must be a lowercase SHA-256 digest")
                safe_metadata[key] = value
            elif key in {"test_count", "passed_count", "failed_count", "duration_ms", "run_revision"}:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"{key} must be a non-negative integer")
                safe_metadata[key] = value
            elif key == "signature_verified":
                if type(value) is not bool:
                    raise ValueError("signature_verified must be boolean")
                safe_metadata[key] = value
            else:
                safe_metadata[key] = _text(value, key, 512) if isinstance(value, str) else value
                if isinstance(safe_metadata[key], str) and re.search(
                    r"(?i)(api[_-]?key|password|secret|bearer[ ]|authorization|oracle[_ -]?answer|private[_ -]?key|token=|prompt=)",
                    safe_metadata[key],
                ):
                    raise ValueError(f"{key} appears to contain secret or raw task content")
                if not isinstance(safe_metadata[key], (str, bool, int, float, type(None))):
                    raise ValueError(f"{key} must be a scalar safe metadata value")
                if isinstance(safe_metadata[key], float) and not math.isfinite(safe_metadata[key]):
                    raise ValueError(f"{key} must be a finite number")
        repository = repository.strip()
        head_sha = head_sha.strip()
        artifact_digest = artifact_digest.strip()
        if head_sha and not _COMMIT_RE.fullmatch(head_sha):
            raise ValueError("head_sha must be a full lowercase commit SHA")
        for value, field in ((artifact_digest, "artifact_digest"),):
            if value and not _SHA_RE.fullmatch(value):
                raise ValueError(f"{field} must be a lowercase SHA-256 digest")
        if evidence_uri and not evidence_uri.startswith(("https://", "file:", "urn:")):
            raise ValueError("evidence_uri must use https, file, or urn")
        if evidence_uri.startswith("https://"):
            parsed_uri = urlparse(evidence_uri)
            if parsed_uri.username or parsed_uri.password or parsed_uri.query:
                raise ValueError("evidence_uri must not embed credentials or query-string secrets")
        stable_event_id = event_id is not None
        base = {
            "event_id": _text(event_id or uuid4().hex, "event_id"),
            "event_type": event_type,
            "source_type": source_type,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "work_unit_id": _text(work_unit_id, "work_unit_id"),
            "task_id": _text(task_id, "task_id"),
            "iteration": iteration,
            "status": _text(status, "status", 64),
            "repository": repository,
            "head_sha": head_sha,
            "artifact_digest": artifact_digest,
            "evidence_uri": evidence_uri,
            "metadata": safe_metadata,
        }
        event: dict[str, Any]
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = None
            if stable_event_id:
                existing = db.execute(
                    "SELECT event_id,event_type,source_type,occurred_at,work_unit_id,task_id,iteration,status,"
                    "repository,head_sha,artifact_digest,evidence_uri,metadata_json,previous_digest,event_digest,signer_id,signature "
                    "FROM engineering_events WHERE event_id=?",
                    (base["event_id"],),
                ).fetchone()
            if existing is not None:
                names = (
                    "event_id", "event_type", "source_type", "occurred_at", "work_unit_id", "task_id",
                    "iteration", "status", "repository", "head_sha", "artifact_digest", "evidence_uri",
                    "metadata_json", "previous_digest", "event_digest", "signer_id", "signature",
                )
                stored = dict(zip(names, existing))
                try:
                    stored_metadata = json.loads(stored.pop("metadata_json"))
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("stored event metadata is corrupt") from exc
                immutable_fields = (
                    "event_id", "event_type", "source_type", "work_unit_id", "task_id", "iteration",
                    "status", "repository", "head_sha", "artifact_digest", "evidence_uri",
                )
                if any(stored[field] != base[field] for field in immutable_fields) or stored_metadata != safe_metadata:
                    raise ValueError("event_id already exists with different immutable content")
                stored_unsigned = {
                    "event_id": stored["event_id"], "event_type": stored["event_type"],
                    "source_type": stored["source_type"], "occurred_at": stored["occurred_at"],
                    "work_unit_id": stored["work_unit_id"], "task_id": stored["task_id"],
                    "iteration": stored["iteration"], "status": stored["status"],
                    "repository": stored["repository"], "head_sha": stored["head_sha"],
                    "artifact_digest": stored["artifact_digest"], "evidence_uri": stored["evidence_uri"],
                    "metadata": stored_metadata, "previous_digest": stored["previous_digest"],
                }
                if sha256_bytes(_canonical(stored_unsigned)) != stored["event_digest"]:
                    raise ValueError("stored event digest is corrupt")
                event = {
                    **stored_unsigned, "event_digest": stored["event_digest"],
                    "signer_id": stored["signer_id"], "signature": stored["signature"],
                }
            else:
                previous = db.execute(
                    "SELECT event_digest FROM engineering_events ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                previous_digest = previous[0] if previous else "0" * 64
                unsigned = {**base, "previous_digest": previous_digest}
                event_digest = sha256_bytes(_canonical(unsigned))
                signer_id, signature = "", ""
                if self.signer:
                    signed = self.signer(bytes.fromhex(event_digest))
                    if not isinstance(signed, Mapping):
                        raise ValueError("signer must return signer_id and signature")
                    signer_id = _text(signed.get("signer_id"), "signer_id")
                    signature = _text(signed.get("signature"), "signature", 4096)
                db.execute(
                    """INSERT INTO engineering_events(
                        event_id,event_type,source_type,occurred_at,work_unit_id,task_id,
                        iteration,status,repository,head_sha,artifact_digest,evidence_uri,
                        metadata_json,previous_digest,event_digest,signer_id,signature
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        base["event_id"], event_type, source_type, base["occurred_at"],
                        base["work_unit_id"], base["task_id"], iteration, base["status"],
                        repository, head_sha, artifact_digest, evidence_uri,
                        json.dumps(safe_metadata, sort_keys=True), previous_digest,
                        event_digest, signer_id, signature,
                    ),
                )
                event = {**unsigned, "event_digest": event_digest, "signer_id": signer_id, "signature": signature}
            if self.sink:
                # Keep the local event and its delivery obligation in one commit.
                db.execute(
                    "INSERT OR IGNORE INTO engineering_event_outbox(event_id,event_json,delivery_state,attempts,last_error,updated_at) VALUES(?,?,'pending',0,'',?)",
                    (base["event_id"], json.dumps(event, sort_keys=True), datetime.now(timezone.utc).isoformat()),
                )
        if self.sink and not _defer_export:
            # Remote delivery is retriable; promotion must gate on pending_exports().
            self.flush_pending(limit=10)
        return event

    def ingest_signed_external_evidence(
        self,
        envelope: Mapping[str, Any],
        *,
        verifier: Any,
        expected_repository: str,
        expected_head: str,
        max_age_seconds: float = 86_400.0,
        clock_skew_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Verify and index an externally signed evidence envelope.

        The signed payload is a reference/attestation, not a place to upload raw
        holdout cases or oracle answers. source_uri must point to the external
        system's retained signed receipt. The caller supplies a verifier backed
        by operator-trusted public keys; this method never accepts a public key
        from the envelope itself.
        """
        if self.evidence_ledger is None:
            raise ValueError("a canonical evidence ledger is required for external evidence")
        if not isinstance(envelope, Mapping) or set(envelope) != {"payload", "signer_id", "signature"}:
            raise ValueError("external envelope must contain exactly payload, signer_id, and signature")
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("external evidence payload must be an object")
        signer_id = _text(envelope.get("signer_id"), "signer_id")
        signature = _text(envelope.get("signature"), "signature", 4096)
        required = {
            "evidence_id", "repository", "head_sha", "evidence_type", "status",
            "source_uri", "payload_digest", "source_type", "issued_at", "run_id",
        }
        allowed = required | {
            "evaluator_id", "oracle_id", "runtime_id", "corpus_digest",
            "isolation_profile", "external_system", "operator_id", "target_id",
            "rollback_plan_digest", "last_known_good_commit", "recovered_head_sha",
        }
        if required - set(payload) or set(payload) - allowed:
            raise ValueError("external evidence payload has missing or unknown fields")
        if payload["source_type"] not in {
            "ci", "external_evaluator", "oracle_custody",
            "sealed_corpus_custody", "isolation_audit", "operator",
        }:
            raise ValueError("external evidence source_type is not an approved external source")
        if payload["repository"] != expected_repository:
            raise ValueError("external evidence repository does not match live trusted context")
        if payload["head_sha"] != expected_head or not _COMMIT_RE.fullmatch(str(expected_head)):
            raise ValueError("external evidence is stale or does not match the live candidate head")
        if signer_id in {
            str(payload.get("evaluator_id", "")),
            str(payload.get("oracle_id", "")),
            str(payload.get("runtime_id", "")),
        }:
            raise ValueError("evaluated runtime, evaluator, and oracle cannot self-attest this envelope")
        if payload["source_type"] == "operator" and payload["evidence_type"] == "rollback_rehearsal":
            required_rollback = {
                "operator_id", "target_id", "rollback_plan_digest",
                "last_known_good_commit", "recovered_head_sha",
            }
            if required_rollback - set(payload):
                raise ValueError("operator rollback receipt is missing required rehearsal fields")
            if not _COMMIT_RE.fullmatch(str(payload["last_known_good_commit"])):
                raise ValueError("last_known_good_commit must be a full commit SHA")
            if not _COMMIT_RE.fullmatch(str(payload["recovered_head_sha"])):
                raise ValueError("recovered_head_sha must be a full commit SHA")
            if payload["status"] in {"passed", "verified"} and payload["recovered_head_sha"] != payload["last_known_good_commit"]:
                raise ValueError("successful rollback receipt must prove recovery to last-known-good")
            if not _SHA_RE.fullmatch(str(payload["rollback_plan_digest"])):
                raise ValueError("rollback_plan_digest must be a SHA-256 digest")
        try:
            issued = datetime.fromisoformat(str(payload["issued_at"]).replace("Z", "+00:00"))
            if issued.tzinfo is None or issued.utcoffset() is None:
                raise ValueError("issued_at must include a timezone")
            age = (datetime.now(timezone.utc) - issued.astimezone(timezone.utc)).total_seconds()
        except (TypeError, OverflowError) as exc:
            raise ValueError("issued_at is invalid") from exc
        if (
            isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float))
            or not math.isfinite(max_age_seconds) or max_age_seconds < 0
            or isinstance(clock_skew_seconds, bool) or not isinstance(clock_skew_seconds, (int, float))
            or not math.isfinite(clock_skew_seconds) or clock_skew_seconds < 0
        ):
            raise ValueError("freshness bounds must be non-negative numbers")
        if age > max_age_seconds or age < -clock_skew_seconds:
            raise ValueError("external evidence is stale or issued too far in the future")
        message = _canonical(payload)
        if hasattr(verifier, "verify_detached"):
            verified = verifier.verify_detached(signer_id, signature, message)
        elif callable(verifier):
            verified = verifier(signer_id, signature, message)
        else:
            raise ValueError("verifier must be a trusted callable or detached-signature verifier")
        if verified is not True:
            raise ValueError("external evidence signature verification failed")

        if "corpus_digest" in payload and not _SHA_RE.fullmatch(str(payload["corpus_digest"])):
            raise ValueError("corpus_digest must be a lowercase SHA-256 digest")
        status = _text(payload["status"], "status", 64)
        if status not in {"passed", "failed", "skipped", "unknown", "verified"}:
            raise ValueError("external evidence status is invalid")
        evidence_id = _text(payload["evidence_id"], "evidence_id")
        source_uri = _text(payload["source_uri"], "source_uri", 2048)
        parsed_source_uri = urlparse(source_uri)
        if source_uri.startswith("https://"):
            if not parsed_source_uri.netloc or parsed_source_uri.username or parsed_source_uri.password or parsed_source_uri.query:
                raise ValueError("source_uri must not embed credentials or query-string secrets")
        elif not source_uri.startswith(("file:", "urn:")):
            raise ValueError("source_uri must use an explicit https, file, or urn URI")
        digest = _text(payload["payload_digest"], "payload_digest", 64)
        if not _SHA_RE.fullmatch(digest):
            raise ValueError("payload_digest must be a lowercase SHA-256 digest")
        evidence_type = _text(payload["evidence_type"], "evidence_type")
        receipt_digest = sha256_bytes(_canonical(envelope))
        metadata: dict[str, Any] = {
            "external_system": _text(payload.get("external_system", payload["source_type"]), "external_system"),
            "run_id": _text(payload["run_id"], "run_id"),
            "signature_key_id": signer_id,
            "receipt_digest": receipt_digest,
            "result_category": status,
            "phase": "external_evidence_ingestion",
        }
        for key in ("evaluator_id", "oracle_id", "isolation_profile", "operator_id", "target_id", "last_known_good_commit", "recovered_head_sha"):
            if key in payload:
                metadata[key] = _text(payload[key], key)
        if "rollback_plan_digest" in payload:
            metadata["rollback_plan_digest"] = _text(payload["rollback_plan_digest"], "rollback_plan_digest", 64)
            if not _SHA_RE.fullmatch(metadata["rollback_plan_digest"]):
                raise ValueError("rollback_plan_digest must be a SHA-256 digest")
        if "corpus_digest" in payload:
            metadata["corpus_digest"] = _text(payload["corpus_digest"], "corpus_digest", 64)
            if not _SHA_RE.fullmatch(metadata["corpus_digest"]):
                raise ValueError("corpus_digest must be a lowercase SHA-256 digest")
        self.evidence_ledger.record(
            evidence_id=evidence_id,
            repository=str(payload["repository"]),
            head_sha=str(payload["head_sha"]),
            evidence_type=evidence_type,
            status=status,
            source_uri=source_uri,
            payload_digest=digest,
            signer_id=signer_id,
        )
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO external_signed_receipts(receipt_digest,evidence_id,signer_id,envelope_json,retained_at) VALUES(?,?,?,?,?)",
                (receipt_digest, evidence_id, signer_id, json.dumps(envelope, sort_keys=True), datetime.now(timezone.utc).isoformat()),
            )
        event = self.record(
            "external_evidence_observed",
            work_unit_id="external:" + evidence_id,
            task_id="external:" + evidence_id,
            iteration=0,
            status=status,
            repository=str(payload["repository"]),
            head_sha=str(payload["head_sha"]),
            artifact_digest=digest,
            evidence_uri=source_uri,
            source_type=str(payload["source_type"]),
            metadata=metadata,
            event_id="external-evidence-" + sha256_bytes(evidence_id.encode("utf-8")),
            _defer_export=True,
        )
        if self.sink is not None:
            # The signed envelope contains only the allowlisted attestation and
            # digests; sealed corpus cases and oracle answers are not accepted by
            # this schema. Ship the envelope to the configured retention sink.
            outbound = {**event, "signed_receipt": dict(envelope)}
            with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
                db.execute(
                    "UPDATE engineering_event_outbox SET event_json=?,updated_at=? WHERE event_id=? AND delivery_state='pending'",
                    (json.dumps(outbound, sort_keys=True), datetime.now(timezone.utc).isoformat(), event["event_id"]),
                )
            self.flush_pending(limit=10)
        return {
            "evidence_id": evidence_id,
            "event_digest": event["event_digest"],
            "receipt_digest": receipt_digest,
            "signature_verified": True,
        }

    def signed_receipts(self, *, evidence_id: str | None = None) -> tuple[dict[str, Any], ...]:
        """Return retained signed envelopes, never sealed corpus/oracle contents."""
        query = "SELECT receipt_digest,evidence_id,signer_id,envelope_json,retained_at FROM external_signed_receipts"
        args: tuple[Any, ...] = ()
        if evidence_id is not None:
            query += " WHERE evidence_id=?"
            args = (evidence_id,)
        query += " ORDER BY retained_at"
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            rows = db.execute(query, args).fetchall()
        return tuple({
            "receipt_digest": row[0], "evidence_id": row[1], "signer_id": row[2],
            "envelope": json.loads(row[3]), "retained_at": row[4],
        } for row in rows)

    def pending_exports(self) -> int:
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            # In-flight and unfamiliar states must keep promotion gates blocked.
            return int(db.execute(
                "SELECT COUNT(*) FROM engineering_event_outbox WHERE delivery_state!='sent'"
            ).fetchone()[0])

    def flush_pending(self, *, limit: int = 100) -> dict[str, int]:
        """Deliver a bounded batch using durable, leased SQLite claims.

        Delivery remains at-least-once: a remote sink can accept an event just before
        this process dies. The stable event ID must be enforced as an idempotency key
        by the remote service.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if self.sink is None:
            return {"sent": 0, "pending": self.pending_exports()}

        claimed_at = datetime.now(timezone.utc)
        claimed_at_text = claimed_at.isoformat()
        lease_expires_at = (
            claimed_at + timedelta(seconds=_OUTBOX_CLAIM_LEASE_SECONDS)
        ).isoformat()
        claimed_rows: list[tuple[str, str, str]] = []

        # Serialize selection and claim so two local flushers cannot both claim an
        # unexpired row. A process death leaves a lease that a later flush can reclaim.
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE engineering_event_outbox
                   SET delivery_state='pending',claim_token='',claimed_at='',
                       lease_expires_at='',updated_at=?
                   WHERE delivery_state='sending'
                     AND (lease_expires_at='' OR lease_expires_at<=?)""",
                (claimed_at_text, claimed_at_text),
            )
            rows = db.execute(
                """SELECT event_id,event_json FROM engineering_event_outbox
                   WHERE delivery_state='pending'
                   ORDER BY updated_at,event_id LIMIT ?""",
                (limit,),
            ).fetchall()
            for event_id, event_json in rows:
                row_claim_token = uuid4().hex
                cursor = db.execute(
                    """UPDATE engineering_event_outbox
                       SET delivery_state='sending',claim_token=?,claimed_at=?,
                           lease_expires_at=?,updated_at=?
                       WHERE event_id=? AND delivery_state='pending'""",
                    (
                        row_claim_token, claimed_at_text, lease_expires_at,
                        claimed_at_text, event_id,
                    ),
                )
                if cursor.rowcount == 1:
                    claimed_rows.append((event_id, event_json, row_claim_token))
            db.commit()

        sent = 0
        for event_id, event_json, row_claim_token in claimed_rows:
            try:
                self.sink(json.loads(event_json))
            except Exception as exc:
                with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
                    # A stale claimant must not overwrite a newer owner's state.
                    db.execute(
                        """UPDATE engineering_event_outbox
                           SET delivery_state='pending',attempts=attempts+1,
                               last_error=?,updated_at=?,claim_token='',claimed_at='',
                               lease_expires_at=''
                           WHERE event_id=? AND delivery_state='sending' AND claim_token=?""",
                        (
                            type(exc).__name__, datetime.now(timezone.utc).isoformat(),
                            event_id, row_claim_token,
                        ),
                    )
            else:
                with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
                    cursor = db.execute(
                        """UPDATE engineering_event_outbox
                           SET delivery_state='sent',attempts=attempts+1,last_error='',
                               updated_at=?,claim_token='',claimed_at='',lease_expires_at=''
                           WHERE event_id=? AND delivery_state='sending' AND claim_token=?""",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            event_id, row_claim_token,
                        ),
                    )
                    # Count only a finalization owned by this exact claim.
                    if cursor.rowcount == 1:
                        sent += 1
        return {"sent": sent, "pending": self.pending_exports()}

    def events(self, *, task_id: str | None = None) -> tuple[dict[str, Any], ...]:
        query = "SELECT event_id,event_type,source_type,occurred_at,work_unit_id,task_id,iteration,status,repository,head_sha,artifact_digest,evidence_uri,metadata_json,previous_digest,event_digest,signer_id,signature FROM engineering_events"
        args: tuple[Any, ...] = ()
        if task_id is not None:
            query += " WHERE task_id=?"
            args = (task_id,)
        query += " ORDER BY sequence"
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            rows = db.execute(query, args).fetchall()
        names = (
            "event_id", "event_type", "source_type", "occurred_at", "work_unit_id",
            "task_id", "iteration", "status", "repository", "head_sha", "artifact_digest",
            "evidence_uri", "metadata_json", "previous_digest", "event_digest", "signer_id", "signature",
        )
        result = []
        for row in rows:
            item = dict(zip(names, row))
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return tuple(result)

    def verify_chain(self) -> bool:
        previous = "0" * 64
        for event in self.events():
            unsigned = {
                key: event[key] for key in (
                    "event_id", "event_type", "source_type", "occurred_at", "work_unit_id",
                    "task_id", "iteration", "status", "repository", "head_sha",
                    "artifact_digest", "evidence_uri", "metadata",
                )
            }
            unsigned["previous_digest"] = previous
            calculated = sha256_bytes(_canonical(unsigned))
            if event["previous_digest"] != previous or event["event_digest"] != calculated:
                return False
            previous = calculated
        return True


class HttpsEvidenceSink:
    """Small HTTPS JSON sink adapter; endpoint/token are operator-supplied."""

    def __init__(
        self, url: str, bearer_token: str, *, timeout_seconds: float = 5.0,
        allowed_cidrs: Sequence[str] = (),
    ) -> None:
        url = _text(url, "sink URL", 2048)
        if not url.startswith("https://"):
            raise ValueError("evidence sink must use HTTPS")
        parsed_url = urlparse(url)
        if not parsed_url.hostname or not parsed_url.netloc:
            raise ValueError("evidence sink URL must include a valid HTTPS host")
        if any(character.isspace() for character in parsed_url.hostname):
            raise ValueError("evidence sink URL must include a valid HTTPS host")
        try:
            parsed_url.port
        except ValueError as exc:
            raise ValueError("evidence sink URL must include a valid HTTPS port") from exc
        if parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
            raise ValueError("evidence sink URL must not embed credentials, query-string secrets, or fragments")
        if not isinstance(bearer_token, str) or not bearer_token.strip():
            raise ValueError("evidence sink bearer token is required")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
            or timeout_seconds > 30
            or not math.isfinite(timeout_seconds)
        ):
            raise ValueError("timeout_seconds must be a finite number greater than 0 and at most 30")
        self.url = url
        self._host = parsed_url.hostname.lower().rstrip(".")
        self.allowed_cidrs = tuple(str(item).strip() for item in allowed_cidrs if str(item).strip())
        self._token = bearer_token
        self.timeout_seconds = timeout_seconds

    def __call__(self, event: Mapping[str, Any]) -> None:
        event_id = event.get("event_id") if isinstance(event, Mapping) else None
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("evidence sink event requires a stable event_id for idempotent delivery")
        try:
            status, _response_body = guarded_https_request(
                self.url,
                allowed_hosts=(self._host,),
                allowed_cidrs=self.allowed_cidrs,
                method="POST",
                body=_canonical(event),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": event_id.strip(),
                    "User-Agent": "AUREN-Engineering-Evidence/1",
                },
                timeout=self.timeout_seconds,
                max_bytes=64 * 1024,
            )
            if not 200 <= status < 300:
                raise RuntimeError(f"evidence sink returned HTTP {status}")
        except (OSError, TimeoutError, ValueError) as exc:
            raise RuntimeError("evidence export failed; see configured sink diagnostics") from exc


__all__ = ["EvidenceCapture", "HttpsEvidenceSink", "sha256_bytes"]