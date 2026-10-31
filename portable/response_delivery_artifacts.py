"""Immutable, content-addressed storage for resumable response delivery."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import tempfile
import threading
from typing import Any

from .agency_release_lifecycle import ArtifactRef, ArtifactStore
from .response_delivery import (
    ResponseChunk, ResponseDeliveryError, ResponseManifest,
    assemble_response, chunk_response,
)


@dataclass(frozen=True, slots=True)
class StoredResponse:
    artifact: ArtifactRef
    manifest: ResponseManifest
    chunks: tuple[ResponseChunk, ...]


class ResponseArtifactRepository:
    """Reuse the canonical ArtifactStore; cache verified manifests/chunks with a byte cap."""

    def __init__(
        self, store: ArtifactStore, *, max_cached_entries: int = 4,
        max_cached_bytes: int = 32_000_000,
    ) -> None:
        if max_cached_entries < 1 or max_cached_bytes < 1:
            raise ValueError("response artifact cache budgets must be positive")
        self.store = store
        self.max_cached_entries = max_cached_entries
        self.max_cached_bytes = max_cached_bytes
        self._cache: OrderedDict[tuple[str, str], StoredResponse] = OrderedDict()
        self._cache_bytes = 0
        self._lock = threading.RLock()

    def _remember(self, key: tuple[str, str], stored: StoredResponse) -> None:
        size = stored.manifest.total_bytes
        if size > self.max_cached_bytes:
            return
        previous = self._cache.pop(key, None)
        if previous is not None:
            self._cache_bytes -= previous.manifest.total_bytes
        while self._cache and (
            len(self._cache) >= self.max_cached_entries
            or self._cache_bytes + size > self.max_cached_bytes
        ):
            _old_key, old = self._cache.popitem(last=False)
            self._cache_bytes -= old.manifest.total_bytes
        self._cache[key] = stored
        self._cache_bytes += size

    def stage(self, text: str, *, max_chunk_bytes: int = 12_000) -> StoredResponse:
        manifest, chunks = chunk_response(text, max_bytes=max_chunk_bytes)
        with tempfile.TemporaryDirectory(prefix="auren-response-artifact-") as directory:
            root = Path(directory)
            chunk_dir = root / "chunks"
            chunk_dir.mkdir()
            index = []
            for chunk in chunks:
                name = f"{chunk.index:08d}.utf8"
                raw = chunk.text.encode("utf-8")
                (chunk_dir / name).write_bytes(raw)
                index.append({
                    "index": chunk.index, "file": "chunks/" + name,
                    "sha256": chunk.sha256, "utf8_bytes": len(raw),
                })
            manifest_payload = {
                "schema_version": 1,
                "response_id": manifest.response_id,
                "sha256": manifest.sha256,
                "total_chunks": manifest.total_chunks,
                "total_chars": manifest.total_chars,
                "total_bytes": manifest.total_bytes,
                "max_chunk_bytes": max_chunk_bytes,
                "chunks": index,
            }
            (root / "manifest.json").write_text(
                json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            ref = self.store.stage(root, "response:" + manifest.response_id)
        stored = StoredResponse(ref, manifest, chunks)
        with self._lock:
            self._remember((manifest.response_id, ref.digest), stored)
        return stored

    def load(self, response_id: str, artifact_digest: str) -> StoredResponse:
        if not isinstance(response_id, str) or not re.fullmatch(r"[0-9a-f]{64}", response_id):
            raise ValueError("response_id must be a lowercase SHA-256 digest")
        if not isinstance(artifact_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_digest):
            raise ValueError("artifact_digest must be a lowercase SHA-256 digest")
        key = (response_id, artifact_digest)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached

            ref = self.store.resolve(artifact_digest)
            if ref.layout != "directory":
                raise ResponseDeliveryError("response artifact must use the directory layout")
            root = Path(ref.path)
            payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                raise ResponseDeliveryError("response artifact manifest schema is invalid")
            if payload.get("response_id") != response_id or payload.get("sha256") != response_id:
                raise ResponseDeliveryError("response artifact identity does not match its manifest")
            total_chunks = payload.get("total_chunks")
            total_chars = payload.get("total_chars")
            total_bytes = payload.get("total_bytes")
            max_chunk_bytes = payload.get("max_chunk_bytes")
            entries = payload.get("chunks")
            if (
                type(total_chunks) is not int or total_chunks < 1
                or type(total_chars) is not int or total_chars < 0
                or type(total_bytes) is not int or total_bytes < 0
                or type(max_chunk_bytes) is not int or max_chunk_bytes < 4
                or not isinstance(entries, list) or len(entries) != total_chunks
            ):
                raise ResponseDeliveryError("response artifact manifest bounds are invalid")
            chunks = []
            for expected, entry in enumerate(entries, start=1):
                if not isinstance(entry, dict) or entry.get("index") != expected:
                    raise ResponseDeliveryError("response artifact chunk index is invalid")
                name = entry.get("file")
                digest = entry.get("sha256")
                byte_count = entry.get("utf8_bytes")
                if (
                    not isinstance(name, str) or name != f"chunks/{expected:08d}.utf8"
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or type(byte_count) is not int or byte_count < 0 or byte_count > max_chunk_bytes
                ):
                    raise ResponseDeliveryError("response artifact chunk metadata is invalid")
                raw = (root / name).read_bytes()
                if len(raw) != byte_count or hashlib.sha256(raw).hexdigest() != digest:
                    raise ResponseDeliveryError("response artifact chunk integrity check failed")
                try:
                    chunk_text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ResponseDeliveryError("response artifact chunk is not valid UTF-8") from exc
                chunks.append(ResponseChunk(response_id, expected, total_chunks, chunk_text, digest))
            manifest = ResponseManifest(response_id, total_chunks, total_chars, total_bytes, response_id)
            assemble_response(manifest, chunks)
            stored = StoredResponse(ref, manifest, tuple(chunks))
            self._remember(key, stored)
            return stored


__all__ = ["ResponseArtifactRepository", "StoredResponse"]
