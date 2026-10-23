"""Operator-owned, append-only index for exact-head engineering evidence.

The ledger is a local trust boundary only when its database and writer identity
are controlled by the operator. A database row is not a cryptographic signature.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from typing import Any, Mapping


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_STATUSES = {"passed", "failed", "skipped", "unknown", "verified"}


class EvidenceConflict(ValueError):
    """An evidence identifier was reused with different immutable content."""


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


class CanonicalEvidenceLedger:
    """Persist evidence references bound to a repository and exact candidate SHA."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path == ":memory:":
            raise ValueError("canonical evidence ledger requires persistent storage")
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path, timeout=10)) as db, db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS canonical_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    head_sha TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_uri TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    signer_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    record_digest TEXT NOT NULL
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS canonical_evidence_head "
                "ON canonical_evidence(repository, head_sha)"
            )

    @staticmethod
    def _normalize(
        evidence_id: str,
        repository: str,
        head_sha: str,
        evidence_type: str,
        status: str,
        source_uri: str,
        payload_digest: str,
        signer_id: str,
    ) -> dict[str, str]:
        values = {
            "evidence_id": _required_text(evidence_id, "evidence_id"),
            "repository": _required_text(repository, "repository"),
            "head_sha": _required_text(head_sha, "head_sha"),
            "evidence_type": _required_text(evidence_type, "evidence_type"),
            "status": _required_text(status, "status"),
            "source_uri": _required_text(source_uri, "source_uri"),
            "payload_digest": _required_text(payload_digest, "payload_digest"),
            "signer_id": _required_text(signer_id, "signer_id"),
        }
        if not _SHA_RE.fullmatch(values["head_sha"]):
            raise ValueError("head_sha must be a full lowercase 40-character commit SHA")
        if values["status"] not in _ALLOWED_STATUSES:
            raise ValueError("status must be passed, failed, skipped, unknown, or verified")
        if not _DIGEST_RE.fullmatch(values["payload_digest"]):
            raise ValueError("payload_digest must be a lowercase SHA-256 digest")
        if not (
            values["source_uri"].startswith("https://")
            or values["source_uri"].startswith("file:")
            or values["source_uri"].startswith("urn:")
        ):
            raise ValueError("source_uri must use an explicit https, file, or urn URI")
        return values

    @staticmethod
    def _validated_record(row: tuple[Any, ...]) -> dict[str, str]:
        keys = (
            "evidence_id", "repository", "head_sha", "evidence_type", "status",
            "source_uri", "payload_digest", "signer_id", "recorded_at", "record_digest",
        )
        record = dict(zip(keys, row))
        body = {key: record[key] for key in keys if key != "record_digest"}
        expected = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if record["record_digest"] != expected:
            raise ValueError("canonical evidence record digest is corrupt")
        return record

    def record(
        self,
        *,
        evidence_id: str,
        repository: str,
        head_sha: str,
        evidence_type: str,
        status: str,
        source_uri: str,
        payload_digest: str,
        signer_id: str,
    ) -> dict[str, str]:
        """Record immutable evidence; identical retries are idempotent."""
        values = self._normalize(
            evidence_id, repository, head_sha, evidence_type, status,
            source_uri, payload_digest, signer_id,
        )
        now = datetime.now(timezone.utc).isoformat()
        record_body = {**values, "recorded_at": now}
        record_digest = hashlib.sha256(
            json.dumps(record_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with closing(sqlite3.connect(self.database_path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT evidence_id,repository,head_sha,evidence_type,status,source_uri,payload_digest,signer_id,recorded_at,record_digest "
                "FROM canonical_evidence WHERE evidence_id=?",
                (values["evidence_id"],),
            ).fetchone()
            if row:
                prior = self._validated_record(row)
                if any(prior[key] != values[key] for key in values):
                    raise EvidenceConflict("evidence_id already exists with different immutable content")
                return prior
            db.execute(
                "INSERT INTO canonical_evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    values["evidence_id"], values["repository"], values["head_sha"],
                    values["evidence_type"], values["status"], values["source_uri"],
                    values["payload_digest"], values["signer_id"], now, record_digest,
                ),
            )
        return {"evidence_id": values["evidence_id"], **record_body, "record_digest": record_digest}

    def known_evidence_ids(self, *, repository: str, head_sha: str) -> tuple[str, ...]:
        """Return only ledgered evidence IDs for the exact live candidate context."""
        repository = _required_text(repository, "repository")
        head_sha = _required_text(head_sha, "head_sha")
        if not _SHA_RE.fullmatch(head_sha):
            raise ValueError("head_sha must be a full lowercase 40-character commit SHA")
        with closing(sqlite3.connect(self.database_path, timeout=10)) as db, db:
            rows = db.execute(
                "SELECT evidence_id,repository,head_sha,evidence_type,status,source_uri,payload_digest,signer_id,recorded_at,record_digest "
                "FROM canonical_evidence WHERE repository=? AND head_sha=? AND status IN ('passed','verified') ORDER BY evidence_id",
                (repository, head_sha),
            ).fetchall()
        records = tuple(self._validated_record(row) for row in rows)
        return tuple(record["evidence_id"] for record in records)

    def resolve(self, evidence_id: str, *, repository: str, head_sha: str) -> dict[str, str] | None:
        """Resolve evidence only when its repository and candidate SHA match."""
        evidence_id = _required_text(evidence_id, "evidence_id")
        repository = _required_text(repository, "repository")
        head_sha = _required_text(head_sha, "head_sha")
        with closing(sqlite3.connect(self.database_path, timeout=10)) as db, db:
            row = db.execute(
                "SELECT evidence_id,repository,head_sha,evidence_type,status,source_uri,payload_digest,signer_id,recorded_at,record_digest "
                "FROM canonical_evidence WHERE evidence_id=? AND repository=? AND head_sha=?",
                (evidence_id, repository, head_sha),
            ).fetchone()
        if not row:
            return None
        return self._validated_record(row)


__all__ = ["CanonicalEvidenceLedger", "EvidenceConflict"]
