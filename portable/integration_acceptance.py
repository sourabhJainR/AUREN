"""Digest-bound integration acceptance receipts with explicit trust labels.

Receipts record test evidence references, never credentials or raw logs. An
operator-attested receipt is not an independent vendor or identity verification.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit


class IntegrationAcceptanceError(ValueError):
    """An acceptance receipt is invalid or cannot be safely persisted."""


_SCHEMA_VERSION = 1
_MAX_FILE_BYTES = 256 * 1024
_MAX_RECEIPTS = 256
_MAX_SCOPE_ITEMS = 32
_MAX_AGE_DAYS = 30
_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCOPE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_KINDS = {"mcp_host", "provider_api", "local_mcp_service", "orchestrator"}
_SOURCES = {"operator_attested", "builtin_probe"}
_STATES = {"pass", "fail"}
_RECEIPT_KEYS = {
    "integration_id", "integration_kind", "status", "product_version",
    "protocol_version", "tested_at", "test_scope", "evidence_ref",
    "evidence_sha256", "source", "receipt_digest",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntegrationAcceptanceError("acceptance receipt file contains duplicate JSON keys")
        result[key] = value
    return result


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise IntegrationAcceptanceError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrationAcceptanceError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IntegrationAcceptanceError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _printable_text(value: Any, field: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if (
        not isinstance(value, str) or (required and not value.strip())
        or len(value) > 128 or not value.isprintable()
    ):
        raise IntegrationAcceptanceError(f"{field} must be a bounded printable string")
    return value


def _evidence_reference(value: Any, *, source: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or not value.isprintable():
        raise IntegrationAcceptanceError("evidence_ref must be a bounded printable reference")
    if source == "builtin_probe" and value == "builtin:probe-local-mcp":
        return value
    try:
        parsed = urlsplit(value)
        if parsed.scheme:
            if (
                parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
            ):
                raise IntegrationAcceptanceError("evidence_ref URL must be HTTPS without credentials, query or fragment")
            return value
    except ValueError as exc:
        raise IntegrationAcceptanceError("evidence_ref is invalid") from exc
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise IntegrationAcceptanceError("evidence_ref must be a safe relative path or HTTPS URL")
    if "\\" in value or ":" in value:
        raise IntegrationAcceptanceError("evidence_ref must not contain path escapes or URI schemes")
    return value


def _record_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "receipt_digest"}


def _validate_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, Mapping) or set(record) != _RECEIPT_KEYS:
        raise IntegrationAcceptanceError("acceptance receipt record schema is invalid")
    integration_id = record.get("integration_id")
    kind = record.get("integration_kind")
    status = record.get("status")
    source = record.get("source")
    if not isinstance(integration_id, str) or not _ID.fullmatch(integration_id):
        raise IntegrationAcceptanceError("integration_id must be a lowercase identifier")
    if kind not in _KINDS:
        raise IntegrationAcceptanceError("integration_kind is unsupported")
    if status not in _STATES:
        raise IntegrationAcceptanceError("status must be pass or fail")
    if source not in _SOURCES:
        raise IntegrationAcceptanceError("source is unsupported")
    product_version = _printable_text(record.get("product_version"), "product_version")
    protocol_version = _printable_text(
        record.get("protocol_version"), "protocol_version", required=False,
    )
    tested_at = _timestamp(record.get("tested_at"), "tested_at")
    scope = record.get("test_scope")
    if (
        not isinstance(scope, list) or not scope or len(scope) > _MAX_SCOPE_ITEMS
        or any(not isinstance(item, str) or not _SCOPE.fullmatch(item) for item in scope)
        or len(scope) != len(set(scope))
    ):
        raise IntegrationAcceptanceError("test_scope must contain unique bounded identifiers")
    evidence_ref = _evidence_reference(record.get("evidence_ref"), source=source)
    evidence_sha256 = record.get("evidence_sha256")
    if not isinstance(evidence_sha256, str) or not _SHA256.fullmatch(evidence_sha256):
        raise IntegrationAcceptanceError("evidence_sha256 must be a lowercase SHA-256 digest")
    if source == "builtin_probe" and (
        kind != "local_mcp_service" or integration_id != "auren-local-mcp"
    ):
        raise IntegrationAcceptanceError("builtin_probe receipts are reserved for the AUREN local MCP service")
    digest = record.get("receipt_digest")
    expected = hashlib.sha256(_canonical(_record_payload(record))).hexdigest()
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest) or digest != expected:
        raise IntegrationAcceptanceError("acceptance receipt digest mismatch")
    return {
        "integration_id": integration_id,
        "integration_kind": kind,
        "status": status,
        "product_version": product_version,
        "protocol_version": protocol_version,
        "tested_at": tested_at.isoformat().replace("+00:00", "Z"),
        "test_scope": list(scope),
        "evidence_ref": evidence_ref,
        "evidence_sha256": evidence_sha256,
        "source": source,
        "receipt_digest": digest,
    }


def _path(project_root: str | Path) -> Path:
    return Path(os.path.abspath(Path(project_root).expanduser())) / ".auren" / "integration-acceptance.json"


def _read_state(path: Path) -> dict[str, Any]:
    if any(parent.is_symlink() for parent in path.parents):
        raise IntegrationAcceptanceError("refusing to read through a symlinked acceptance receipt directory")
    if path.is_symlink():
        raise IntegrationAcceptanceError("refusing to read a symlinked acceptance receipt file")
    if not path.exists():
        return {"schema_version": _SCHEMA_VERSION, "revision": 0, "receipts": []}
    if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
        raise IntegrationAcceptanceError("acceptance receipt file is not a bounded regular file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IntegrationAcceptanceError("acceptance receipt file is not valid JSON") from exc
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "revision", "receipts"}
        or type(data.get("schema_version")) is not int or data["schema_version"] != _SCHEMA_VERSION
        or type(data.get("revision")) is not int or data["revision"] < 0
        or not isinstance(data.get("receipts"), list)
        or len(data["receipts"]) > _MAX_RECEIPTS
    ):
        raise IntegrationAcceptanceError("acceptance receipt file schema is invalid")
    seen: set[tuple[str, str]] = set()
    for raw in data["receipts"]:
        record = _validate_record(raw)
        key = (record["integration_kind"], record["integration_id"])
        if key in seen:
            raise IntegrationAcceptanceError("acceptance receipt file contains duplicate integration records")
        seen.add(key)
    return data


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(parent.is_symlink() for parent in path.parents):
        raise IntegrationAcceptanceError("refusing to lock through a symlinked acceptance receipt directory")
    lock_path = path.with_name(path.name + ".lock")
    if lock_path.is_symlink():
        raise IntegrationAcceptanceError("refusing a symlinked acceptance receipt lock")
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise IntegrationAcceptanceError("another acceptance receipt update is active or a stale lock needs review") from exc
    try:
        os.write(lock_fd, str(os.getpid()).encode("ascii"))
        os.fsync(lock_fd)
        os.close(lock_fd)
        yield
    finally:
        try:
            os.close(lock_fd)
        except OSError:
            pass
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    payload = _canonical(state) + b"\n"
    if len(payload) > _MAX_FILE_BYTES:
        raise IntegrationAcceptanceError("acceptance receipt file exceeds the maximum size")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(parent.is_symlink() for parent in path.parents) or path.is_symlink():
        raise IntegrationAcceptanceError("refusing to write through a symlinked acceptance receipt path")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def record_integration_acceptance(
    project_root: str | Path,
    *,
    integration_id: str,
    integration_kind: str,
    status: str,
    product_version: str,
    protocol_version: str | None,
    test_scope: list[str] | tuple[str, ...],
    evidence_ref: str,
    evidence_sha256: str,
    source: str = "operator_attested",
    confirm: bool = False,
    apply: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Preview or persist one digest-bound receipt; writes require explicit confirmation."""
    if type(confirm) is not bool or type(apply) is not bool:
        raise IntegrationAcceptanceError("confirm and apply must be booleans")
    if source == "operator_attested" and apply and confirm is not True:
        raise IntegrationAcceptanceError("operator-attested receipt writes require explicit confirmation")
    if source not in _SOURCES:
        raise IntegrationAcceptanceError("source is unsupported")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise IntegrationAcceptanceError("now must be timezone-aware")
    tested_at = current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    scope = list(test_scope) if isinstance(test_scope, (list, tuple)) else test_scope
    payload = {
        "integration_id": integration_id,
        "integration_kind": integration_kind,
        "status": status,
        "product_version": product_version,
        "protocol_version": protocol_version,
        "tested_at": tested_at,
        "test_scope": scope,
        "evidence_ref": evidence_ref,
        "evidence_sha256": evidence_sha256,
        "source": source,
    }
    record = {
        **payload,
        "receipt_digest": hashlib.sha256(_canonical(payload)).hexdigest(),
    }
    record = _validate_record(record)
    path = _path(project_root)
    if not apply:
        return {"status": "preview", "path": str(path), "receipt": record}
    with _exclusive_lock(path):
        state = _read_state(path)
        key = (record["integration_kind"], record["integration_id"])
        receipts = [
            item for item in state["receipts"]
            if (item["integration_kind"], item["integration_id"]) != key
        ]
        receipts.append(record)
        if len(receipts) > _MAX_RECEIPTS:
            raise IntegrationAcceptanceError("acceptance receipt count exceeds the maximum")
        state["revision"] += 1
        state["receipts"] = sorted(receipts, key=lambda item: (item["integration_kind"], item["integration_id"]))
        _write_state(path, state)
        revision = state["revision"]
    return {"status": "recorded", "path": str(path), "revision": revision, "receipt": record}


def integration_acceptance_report(
    project_root: str | Path = ".",
    *,
    now: datetime | None = None,
    max_age_days: int = _MAX_AGE_DAYS,
) -> dict[str, Any]:
    """Return validated, freshness-aware receipts without claiming independent verification."""
    if type(max_age_days) is not int or not 1 <= max_age_days <= 365:
        raise IntegrationAcceptanceError("max_age_days must be an integer between 1 and 365")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise IntegrationAcceptanceError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)
    path = _path(project_root)
    state = _read_state(path)
    receipts = []
    summary = {"fresh_pass": 0, "fresh_fail": 0, "stale": 0, "invalid": 0}
    for raw in state["receipts"]:
        record = _validate_record(raw)
        tested_at = _timestamp(record["tested_at"], "tested_at")
        if tested_at > current:
            freshness = "invalid"
        elif (current - tested_at).total_seconds() > max_age_days * 86400:
            freshness = "stale"
        else:
            freshness = "fresh"
        if freshness == "stale":
            summary["stale"] += 1
        elif freshness == "invalid":
            summary["invalid"] += 1
        elif freshness == "fresh" and record["status"] == "pass":
            summary["fresh_pass"] += 1
        elif freshness == "fresh" and record["status"] == "fail":
            summary["fresh_fail"] += 1
        trust_level = (
            "operator_attested_not_independently_verified"
            if record["source"] == "operator_attested"
            else "builtin_probe_not_external_host_acceptance"
        )
        receipts.append({
            **record,
            "freshness": freshness,
            "trust_level": trust_level,
        })
    return {
        "status": "not_recorded" if not receipts else "recorded",
        "path": str(path),
        "revision": state["revision"],
        "max_age_days": max_age_days,
        "receipts": receipts,
        "summary": summary,
        "verification_note": (
            "Receipt digests detect accidental changes. Operator-attested receipts are self-reported; "
            "they do not prove vendor identity, OAuth scope, independent security or production readiness."
        ),
    }


__all__ = [
    "IntegrationAcceptanceError",
    "integration_acceptance_report",
    "record_integration_acceptance",
]
