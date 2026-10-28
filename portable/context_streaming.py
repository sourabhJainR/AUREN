"""Bounded, resumable input processing for AUREN.

The active model context is a working set, not the source of truth. Large inputs
stay in files; callers stream deterministic chunks and persist completed results
in a journal. Sampling is opt-in and explicitly forbidden for verification work.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass(frozen=True)
class InputChunk:
    index: int
    start: int
    end: int
    text: str
    sha256: str


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cut_at_boundary(text: str, limit: int) -> int:
    """Choose a stable cut near limit without sacrificing forward progress."""
    if len(text) <= limit:
        return len(text)
    floor = max(1, limit * 3 // 4)
    candidates = [text.rfind(mark, floor, limit) for mark in ("\n", ". ", "; ", " ", "\t")]
    cut = max(candidates)
    return cut + 1 if cut >= floor else limit


def iter_text_chunks(text: str, *, max_chars: int = 12_000, overlap_chars: int = 300) -> Iterator[InputChunk]:
    """Chunk an in-memory string with stable boundaries and bounded overlap."""
    _validate_limits(max_chars, overlap_chars)
    offset = 0
    index = 0
    while offset < len(text):
        end = min(len(text), offset + max_chars)
        if end < len(text):
            end = offset + _cut_at_boundary(text[offset:offset + max_chars], max_chars)
        piece = text[offset:end]
        if not piece:
            raise RuntimeError("chunker failed to make forward progress")
        yield InputChunk(index, offset, end, piece, _digest(piece))
        index += 1
        if end == len(text):
            break
        offset = max(offset + 1, end - overlap_chars)


def iter_file_chunks(path: str | Path, *, max_chars: int = 12_000, overlap_chars: int = 300,
                     read_chars: int = 16_384) -> Iterator[InputChunk]:
    """Stream a UTF-8 file without loading the whole file into memory.

    Memory use is bounded by approximately max_chars + read_chars + overlap_chars.
    Offsets are Unicode character offsets, not byte offsets.
    """
    _validate_limits(max_chars, overlap_chars)
    if read_chars < 1:
        raise ValueError("read_chars must be positive")
    buffer = ""
    base = 0
    index = 0
    with Path(path).open("r", encoding="utf-8", errors="strict", newline="") as stream:
        eof = False
        while not eof:
            block = stream.read(read_chars)
            eof = block == ""
            buffer += block
            while len(buffer) > max_chars or (eof and buffer):
                limit = min(max_chars, len(buffer))
                cut = _cut_at_boundary(buffer, limit) if len(buffer) > limit else limit
                piece = buffer[:cut]
                if not piece:
                    raise RuntimeError("chunker failed to make forward progress")
                yield InputChunk(index, base, base + len(piece), piece, _digest(piece))
                index += 1
                if cut >= len(buffer):
                    base += cut
                    buffer = ""
                    break
                keep_from = max(0, cut - overlap_chars)
                base += keep_from
                buffer = buffer[keep_from:]
            # If the remaining tail is exactly max_chars, wait for more data so
            # a boundary can be chosen consistently rather than splitting early.


def _validate_limits(max_chars: int, overlap_chars: int) -> None:
    if max_chars < 32:
        raise ValueError("max_chars must be at least 32")
    if overlap_chars < 0 or overlap_chars >= max_chars // 2:
        raise ValueError("overlap_chars must be non-negative and less than half max_chars")


def choose_sample(items: Iterable[InputChunk], *, count: int, exploratory: bool = False,
                  verification: bool = False) -> list[InputChunk]:
    """Return a deterministic spread sample only for explicitly exploratory work."""
    values = list(items)
    if not exploratory or verification:
        raise PermissionError("sampling is allowed only for explicitly exploratory, non-verification work")
    if count < 1:
        raise ValueError("count must be positive")
    if len(values) <= count:
        return values
    if count == 1:
        return [values[0]]
    indices = sorted({round(i * (len(values) - 1) / (count - 1)) for i in range(count)})
    return [values[i] for i in indices]


class DurableChunkJournal:
    """SQLite journal for resumable chunk work; source changes invalidate resume."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS context_jobs (
                    job_id TEXT PRIMARY KEY,
                    source_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('working','blocked','complete'))
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS context_chunks (
                    job_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_digest TEXT NOT NULL,
                    result TEXT NOT NULL,
                    PRIMARY KEY(job_id, chunk_index),
                    FOREIGN KEY(job_id) REFERENCES context_jobs(job_id)
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=5.0)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def begin(self, job_id: str, source_digest: str) -> None:
        if not job_id.strip() or not source_digest.strip():
            raise ValueError("job_id and source_digest are required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT source_digest FROM context_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row and row[0] != source_digest:
                raise ValueError("source digest changed; start a new job ID instead of resuming stale work")
            db.execute("INSERT OR IGNORE INTO context_jobs(job_id,source_digest,state) VALUES(?,?,'working')",
                       (job_id, source_digest))

    def completed(self, job_id: str) -> dict[int, tuple[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT chunk_index,chunk_digest,result FROM context_chunks WHERE job_id=? ORDER BY chunk_index",
                (job_id,),
            ).fetchall()
        return {int(i): (str(digest), str(result)) for i, digest, result in rows}

    def record(self, job_id: str, chunk: InputChunk, result: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT state FROM context_jobs WHERE job_id=?", (job_id,)).fetchone()
            if not job or job[0] != "working":
                raise ValueError("job is missing or not accepting chunk results")
            prior = db.execute(
                "SELECT chunk_digest,result FROM context_chunks WHERE job_id=? AND chunk_index=?",
                (job_id, chunk.index),
            ).fetchone()
            if prior:
                if prior != (chunk.sha256, result):
                    raise ValueError("conflicting replay for an already recorded chunk")
                return
            db.execute("INSERT INTO context_chunks(job_id,chunk_index,chunk_digest,result) VALUES(?,?,?,?)",
                       (job_id, chunk.index, chunk.sha256, result))

    def finish(self, job_id: str, *, blocked: bool = False) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = "blocked" if blocked else "complete"
            changed = db.execute("UPDATE context_jobs SET state=? WHERE job_id=? AND state='working'",
                                 (state, job_id)).rowcount
            if changed != 1:
                raise ValueError("job is missing or already terminal")


def file_sha256(path: str | Path, *, read_bytes: int = 1 << 20) -> str:
    """Hash a source incrementally so job identity does not require loading it."""
    if read_bytes < 1:
        raise ValueError("read_bytes must be positive")
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(read_bytes), b""):
            digest.update(block)
    return digest.hexdigest()
