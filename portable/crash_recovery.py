"""Crash-safe episode checkpoint store with conflict-detecting idempotent writes."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Mapping


class CheckpointConflict(ValueError):
    """A checkpoint identity was reused with different immutable content."""


@dataclass(frozen=True, slots=True)
class Checkpoint:
    episode_id: str
    sequence: int
    state: str
    payload: dict[str, Any]
    digest: str


class CheckpointStore:
    """SQLite-backed checkpoints with atomic writes and fail-closed duplicates."""

    def __init__(self, db: str | Path = ":memory:"):
        self.db = str(db)
        self.cx = sqlite3.connect(self.db, timeout=5.0)
        self.cx.execute("PRAGMA busy_timeout=5000")
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA synchronous=FULL")
        self.cx.execute(
            "CREATE TABLE IF NOT EXISTS checkpoints("
            "episode TEXT NOT NULL, seq INTEGER NOT NULL, state TEXT NOT NULL, "
            "payload TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(episode,seq))"
        )
        self.cx.commit()

    @staticmethod
    def _canonical_payload(payload: Mapping[str, Any]) -> str:
        if not isinstance(payload, Mapping):
            raise ValueError("checkpoint payload must be a mapping")
        try:
            return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("checkpoint payload must be finite JSON-compatible data") from exc

    @staticmethod
    def _digest(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_identity(episode_id: str, sequence: int, state: str) -> None:
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ValueError("episode_id must be non-empty text")
        if type(sequence) is not int or sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if not isinstance(state, str) or not state.strip():
            raise ValueError("state must be non-empty text")

    def save(
        self, episode_id: str, sequence: int, state: str, payload: Mapping[str, Any]
    ) -> Checkpoint:
        self._validate_identity(episode_id, sequence, state)
        raw = self._canonical_payload(payload)
        digest = self._digest(raw)
        try:
            self.cx.execute("BEGIN IMMEDIATE")
            row = self.cx.execute(
                "SELECT state,payload,digest FROM checkpoints WHERE episode=? AND seq=?",
                (episode_id, sequence),
            ).fetchone()
            if row is not None:
                stored_state, stored_raw, stored_digest = row
                if (stored_state, stored_raw, stored_digest) != (state, raw, digest):
                    self.cx.rollback()
                    raise CheckpointConflict(
                        "checkpoint identity already exists with different immutable content"
                    )
                self.cx.commit()
                return Checkpoint(episode_id, sequence, stored_state, json.loads(stored_raw), stored_digest)
            self.cx.execute(
                "INSERT INTO checkpoints(episode,seq,state,payload,digest) VALUES(?,?,?,?,?)",
                (episode_id, sequence, state, raw, digest),
            )
            self.cx.commit()
            return Checkpoint(episode_id, sequence, state, json.loads(raw), digest)
        except BaseException:
            if self.cx.in_transaction:
                self.cx.rollback()
            raise

    def latest(self, episode_id: str) -> Checkpoint | None:
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ValueError("episode_id must be non-empty text")
        row = self.cx.execute(
            "SELECT seq,state,payload,digest FROM checkpoints WHERE episode=? ORDER BY seq DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
        if row is None:
            return None
        sequence, state, raw, digest = row
        if self._digest(raw) != digest:
            raise ValueError("checkpoint integrity failure")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("checkpoint payload is corrupt") from exc
        if not isinstance(payload, dict):
            raise ValueError("checkpoint payload is not an object")
        return Checkpoint(episode_id, sequence, state, payload, digest)

    def close(self) -> None:
        self.cx.close()

    def __enter__(self) -> "CheckpointStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["Checkpoint", "CheckpointConflict", "CheckpointStore"]
