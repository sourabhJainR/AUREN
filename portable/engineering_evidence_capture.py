"""Automatic, privacy-minimizing engineering evidence capture.

Capture local lifecycle/iteration metadata without a per-task prompt. Raw prompts,
payloads, source contents, credentials, oracle answers, and sealed corpus data are
never accepted by the event API. Remote export is only enabled by an operator-
configured HTTPS sink; independent claims require verified external attestations.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


_EVENT_TYPES = frozenset({
    "task_submitted", "task_attempt_started", "regression_passed",
    "regression_failed", "recovery_selected", "task_completed", "task_failed",
    "external_evidence_observed", "rollback_started", "rollback_completed",
    "rollback_failed",
})
_SOURCE_TYPES = frozenset({
    "auren_runtime", "ci", "external_evaluator", "oracle_custody",
    "sealed_corpus_custody", "isolation_audit", "operator",
})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


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
    ) -> None:
        self.path = Path(database_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sink = sink
        self.signer = signer
        with sqlite3.connect(self.path, timeout=10) as db:
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

    @classmethod
    def from_environment(cls, database_path: str | Path) -> "EvidenceCapture":
        """Enable automatic HTTPS export only when an operator configured a sink.

        No remote network access occurs merely because this class is imported.
        A signer can be supplied by a KMS/HSM adapter by the hosting runtime.
        """
        url = os.environ.get("AUREN_EVIDENCE_SINK_URL", "").strip()
        token = os.environ.get("AUREN_EVIDENCE_SINK_TOKEN", "")
        sink = HttpsEvidenceSink(url, token) if url else None
        return cls(database_path, sink=sink)

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
    ) -> dict[str, Any]:
        if event_type not in _EVENT_TYPES:
            raise ValueError("unsupported engineering event type")
        if source_type not in _SOURCE_TYPES:
            raise ValueError("unsupported evidence source type")
        if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 0:
            raise ValueError("iteration must be a non-negative integer")
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be a mapping of non-sensitive structured values")
        # Allowlist prevents raw task payloads, prompts, test output, secrets, and
        # oracle labels from being accidentally serialized into the evidence store.
        allowed_metadata = {
            "phase", "handler", "failure_class", "hypothesis_digest",
            "test_suite", "test_count", "passed_count", "failed_count",
            "duration_ms", "retry_action", "external_system", "run_id",
            "evaluator_id", "oracle_id", "corpus_digest", "isolation_profile",
            "signature_verified", "signature_key_id", "rollback_plan_digest",
            "previous_head_sha", "result_category",
        }
        if set(metadata) - allowed_metadata:
            raise ValueError("metadata contains fields outside the safe evidence allowlist")
        safe_metadata: dict[str, Any] = {}
        for key, value in metadata.items():
            if key.endswith("_digest"):
                if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
                    raise ValueError(f"{key} must be a lowercase SHA-256 digest")
                safe_metadata[key] = value
            elif key in {"test_count", "passed_count", "failed_count", "duration_ms"}:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"{key} must be a non-negative integer")
                safe_metadata[key] = value
            elif key == "signature_verified":
                if type(value) is not bool:
                    raise ValueError("signature_verified must be boolean")
                safe_metadata[key] = value
            else:
                safe_metadata[key] = _text(value, key, 512) if isinstance(value, str) else value
                if not isinstance(safe_metadata[key], (str, bool, int, float, type(None))):
                    raise ValueError(f"{key} must be a scalar safe metadata value")
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
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
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
            # Sink errors are not swallowed: production deployments must choose
            # whether export failure blocks consequential promotion. The runtime
            # should fail closed for promotion if required evidence cannot export.
            self.sink(event)
        return event

    def events(self, *, task_id: str | None = None) -> tuple[dict[str, Any], ...]:
        query = "SELECT event_id,event_type,source_type,occurred_at,work_unit_id,task_id,iteration,status,repository,head_sha,artifact_digest,evidence_uri,metadata_json,previous_digest,event_digest,signer_id,signature FROM engineering_events"
        args: tuple[Any, ...] = ()
        if task_id is not None:
            query += " WHERE task_id=?"
            args = (task_id,)
        query += " ORDER BY sequence"
        with sqlite3.connect(self.path, timeout=10) as db:
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

    def __init__(self, url: str, bearer_token: str, *, timeout_seconds: float = 5.0) -> None:
        url = _text(url, "sink URL", 2048)
        if not url.startswith("https://"):
            raise ValueError("evidence sink must use HTTPS")
        if not bearer_token:
            raise ValueError("evidence sink bearer token is required")
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        self.url = url
        self._token = bearer_token
        self.timeout_seconds = timeout_seconds

    def __call__(self, event: Mapping[str, Any]) -> None:
        request = Request(
            self.url,
            data=_canonical(event),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "AUREN-Engineering-Evidence/1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"evidence sink returned HTTP {response.status}")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError("evidence export failed; see configured sink diagnostics") from exc


__all__ = ["EvidenceCapture", "HttpsEvidenceSink", "sha256_bytes"]
