"""Lossless memory packing primitives for AUREN.

Text memories are not TurboQuant vectors. This module applies reversible
compression to snapshots and bounded, evidence-linked chunking to model context.
It never summarizes, truncates, or silently drops a memory record.
"""
from __future__ import annotations

import hashlib
import json
import zlib
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Sequence

SNAPSHOT_MAGIC = b"AUREN-MEMORY-ZLIB-V1\n"
PROTECTED_CATEGORIES = frozenset({
    "acceptance", "constraint", "constraints", "guardrail", "guardrails",
    "security", "policy", "rollback", "failure", "negative_evidence",
    "requirement", "requirements", "invariant", "invariants",
})


class MemoryBudgetExceeded(ValueError):
    """Raised when the caller's context budget cannot fit an indivisible header."""


@dataclass(frozen=True)
class MemoryChunk:
    index: int
    total: int
    text: str
    record_ids: tuple[str, ...]
    source_digest: str


@dataclass(frozen=True)
class CompactSnapshot:
    payload: bytes
    source_digest: str
    original_bytes: int
    compressed_bytes: int
    record_count: int


def _record_dict(record: object) -> dict:
    if hasattr(record, "__dataclass_fields__"):
        value = asdict(record)
    elif isinstance(record, Mapping):
        value = dict(record)
    else:
        raise TypeError("memory records must be dataclasses or mappings")
    # Canonical JSON accepts only plain serializable record fields.
    json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return value


def compress_snapshot(records: Iterable[object], *, level: int = 9) -> CompactSnapshot:
    """Create a deterministic, lossless compressed snapshot; never mutate source memory."""
    if type(level) is not int or not 0 <= level <= 9:
        raise ValueError("compression level must be an integer from 0 through 9")
    rows = [_record_dict(row) for row in records]
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    envelope = json.dumps(
        {"version": 1, "sha256": digest, "records": len(rows), "payload": raw.decode("utf-8")},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    packed = SNAPSHOT_MAGIC + zlib.compress(envelope, level)
    return CompactSnapshot(packed, digest, len(raw), len(packed), len(rows))


def decompress_snapshot(payload: bytes) -> list[dict]:
    """Restore a snapshot only after validating version, record count, and content digest."""
    if not isinstance(payload, bytes) or not payload.startswith(SNAPSHOT_MAGIC):
        raise ValueError("unknown or malformed AUREN memory snapshot")
    try:
        envelope = json.loads(zlib.decompress(payload[len(SNAPSHOT_MAGIC):]).decode("utf-8"))
        if envelope.get("version") != 1 or not isinstance(envelope.get("payload"), str):
            raise ValueError("unsupported AUREN memory snapshot version")
        raw = envelope["payload"].encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != envelope.get("sha256"):
            raise ValueError("AUREN memory snapshot digest mismatch")
        rows = json.loads(envelope["payload"])
        if not isinstance(rows, list) or len(rows) != envelope.get("records"):
            raise ValueError("AUREN memory snapshot record count mismatch")
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("AUREN memory snapshot contains a malformed record")
        return rows
    except (UnicodeDecodeError, zlib.error, json.JSONDecodeError, AttributeError, TypeError) as exc:
        raise ValueError("corrupt AUREN memory snapshot") from exc


def _capsule(record: dict) -> str:
    rid = str(record.get("id", ""))
    category = str(record.get("category", "unknown"))
    verified = "true" if record.get("verified") is True else "false"
    confidence = record.get("confidence", 0.0)
    revision = record.get("source_revision") or "unbound"
    text = record.get("text", "")
    if not isinstance(text, str):
        raise TypeError("memory record text must be a string")
    header = f"[MEMORY id={rid} category={category} verified={verified} confidence={confidence} revision={revision}]\n"
    footer = "\n[/MEMORY]\n"
    return header + text + footer


def chunk_memory_context(
    records: Iterable[object],
    *,
    max_chars: int,
    protected_categories: Sequence[str] = tuple(PROTECTED_CATEGORIES),
) -> list[MemoryChunk]:
    """Pack every retrieved record into bounded chunks without loss or silent omission.

    Long records are split only at Unicode code-point boundaries. Their record ID,
    source digest, and part marker are retained in each chunk for exact reassembly.
    """
    if type(max_chars) is not int or max_chars < 128:
        raise ValueError("max_chars must be an integer >= 128")
    rows = [_record_dict(row) for row in records]
    normalized_protected = {str(item).strip().lower() for item in protected_categories}
    # Deterministic ordering puts mandatory policy/evidence categories first, then
    # verified and high-confidence records. All records are still emitted.
    rows.sort(key=lambda r: (
        str(r.get("category", "")).strip().lower() not in normalized_protected,
        not bool(r.get("verified")),
        -float(r.get("confidence", 0.0) or 0.0),
        str(r.get("created_at", "")),
        str(r.get("id", "")),
    ))
    chunks: list[tuple[str, list[str]]] = []
    current = ""
    current_ids: list[str] = []
    for row in rows:
        capsule = _capsule(row)
        rid = str(row.get("id", ""))
        digest = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        # Keep normal records together when possible. For a large record, emit
        # explicit numbered fragments that can be reassembled without guessing.
        if len(capsule) <= max_chars:
            if current and len(current) + len(capsule) > max_chars:
                chunks.append((current, current_ids))
                current, current_ids = "", []
            current += capsule
            current_ids.append(rid)
            continue
        if current:
            chunks.append((current, current_ids))
            current, current_ids = "", []
        prefix = f"[MEMORY_PART id={rid} sha256={digest} "
        suffix = "]\n"
        body = str(row.get("text", ""))
        fixed = len(prefix) + len("part=999999/999999") + len(suffix)
        width = max_chars - fixed
        if width < 1:
            raise MemoryBudgetExceeded("context budget is too small for a memory chunk header")
        pieces = [body[i:i + width] for i in range(0, len(body), width)] or [""]
        total = len(pieces)
        for part_index, piece in enumerate(pieces, 1):
            text = f"{prefix}part={part_index}/{total}{suffix}{piece}\n[/MEMORY_PART]\n"
            if len(text) > max_chars:
                raise MemoryBudgetExceeded("context budget cannot fit a memory chunk header")
            chunks.append((text, [rid]))
    if current:
        chunks.append((current, current_ids))
    total = len(chunks)
    return [
        MemoryChunk(i + 1, total, text, tuple(ids), hashlib.sha256(text.encode("utf-8")).hexdigest())
        for i, (text, ids) in enumerate(chunks)
    ]


__all__ = [
    "CompactSnapshot", "MemoryBudgetExceeded", "MemoryChunk",
    "compress_snapshot", "decompress_snapshot", "chunk_memory_context",
]
