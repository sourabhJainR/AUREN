"""Lossless, bounded response delivery for large AUREN artifacts.

Chunking is a transport strategy, not permission to omit content. Every chunk is
content-addressed, ordered, byte-bounded and reassemblable with an end-to-end
digest. This module performs no model/provider calls and stores no user content.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Sequence


class ResponseDeliveryError(ValueError):
    """Raised when a response is incomplete, reordered, corrupted or over budget."""


@dataclass(frozen=True, slots=True)
class ResponseChunk:
    response_id: str
    index: int
    total: int
    text: str
    sha256: str

    @property
    def utf8_bytes(self) -> int:
        return len(self.text.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class ResponseManifest:
    response_id: str
    total_chunks: int
    total_chars: int
    total_bytes: int
    sha256: str


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _preferred_boundary(text: str, start: int, hard_end: int) -> int:
    """Prefer paragraph/line/word boundaries without making progress unbounded."""
    floor = start + max(1, (hard_end - start) * 3 // 4)
    for separator in ("\n\n", "\n", " "):
        boundary = text.rfind(separator, floor, hard_end)
        if boundary > start:
            end = boundary + len(separator)
            if end <= hard_end:
                return end
    return hard_end


def chunk_response(text: str, *, max_bytes: int = 12_000) -> tuple[ResponseManifest, tuple[ResponseChunk, ...]]:
    """Split text losslessly into UTF-8 byte-bounded chunks.

    Semantic boundaries are preferred. A single oversized line/code token is
    split at Unicode code-point boundaries. Empty output is represented by one
    empty chunk so completion is unambiguous.
    """
    if not isinstance(text, str):
        raise TypeError("response text must be a string")
    if type(max_bytes) is not int or max_bytes < 4:
        raise ValueError("max_bytes must be an integer >= 4")
    response_id = _digest(text)
    parts: list[str] = []
    start = 0
    while start < len(text):
        low, high = start + 1, len(text)
        # Find the largest code-point boundary whose UTF-8 encoding fits.
        while low < high:
            mid = (low + high + 1) // 2
            if len(text[start:mid].encode("utf-8")) <= max_bytes:
                low = mid
            else:
                high = mid - 1
        hard_end = low
        if hard_end <= start:
            raise ResponseDeliveryError("one Unicode code point exceeds byte budget")
        end = _preferred_boundary(text, start, hard_end)
        parts.append(text[start:end])
        start = end
    if not parts:
        parts = [""]
    total = len(parts)
    chunks = tuple(
        ResponseChunk(response_id, index, total, part, _digest(part))
        for index, part in enumerate(parts, start=1)
    )
    manifest = ResponseManifest(
        response_id=response_id,
        total_chunks=total,
        total_chars=len(text),
        total_bytes=len(text.encode("utf-8")),
        sha256=response_id,
    )
    # Guard our own contract: never return a chunk beyond the transport budget.
    if any(chunk.utf8_bytes > max_bytes for chunk in chunks):
        raise ResponseDeliveryError("internal chunk-size invariant violated")
    return manifest, chunks


def chunks_after(chunks: Sequence[ResponseChunk], acknowledged_index: int) -> tuple[ResponseChunk, ...]:
    """Return only unacknowledged chunks after validating stable sequence metadata."""
    if type(acknowledged_index) is not int or acknowledged_index < 0:
        raise ValueError("acknowledged_index must be a non-negative integer")
    _validate_sequence(chunks)
    if acknowledged_index > len(chunks):
        raise ResponseDeliveryError("acknowledgement is beyond the response end")
    return tuple(chunks[acknowledged_index:])


def _validate_sequence(chunks: Sequence[ResponseChunk]) -> None:
    if not chunks:
        raise ResponseDeliveryError("no chunks supplied")
    first = chunks[0]
    if first.total != len(chunks):
        raise ResponseDeliveryError("missing response chunks")
    expected_id = first.response_id
    for expected_index, chunk in enumerate(chunks, start=1):
        if chunk.response_id != expected_id or chunk.total != len(chunks) or chunk.index != expected_index:
            raise ResponseDeliveryError("response chunks are reordered, duplicated or from different responses")
        if _digest(chunk.text) != chunk.sha256:
            raise ResponseDeliveryError(f"chunk {expected_index} digest mismatch")


def assemble_response(manifest: ResponseManifest, chunks: Iterable[ResponseChunk]) -> str:
    """Reassemble and verify exact character count, byte count and whole-response digest."""
    ordered = tuple(chunks)
    _validate_sequence(ordered)
    if manifest.total_chunks != len(ordered) or manifest.response_id != ordered[0].response_id:
        raise ResponseDeliveryError("manifest does not match chunk sequence")
    text = "".join(chunk.text for chunk in ordered)
    if len(text) != manifest.total_chars or len(text.encode("utf-8")) != manifest.total_bytes:
        raise ResponseDeliveryError("response length mismatch")
    if _digest(text) != manifest.sha256 or _digest(text) != manifest.response_id:
        raise ResponseDeliveryError("response digest mismatch")
    return text


__all__ = [
    "ResponseChunk", "ResponseDeliveryError", "ResponseManifest",
    "assemble_response", "chunk_response", "chunks_after",
]
