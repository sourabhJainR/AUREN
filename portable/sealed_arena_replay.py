"""Durable replay prevention for the canonical sealed Arena exchange boundary."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class ReplayRejected(ValueError):
    """A campaign nonce has already been claimed."""


class SealedArenaReplayLedger:
    """Persistent evaluator-owned nonce ledger; share one database across restarts."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path == ":memory:":
            raise ValueError("replay ledger requires persistent storage")
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS sealed_arena_nonce_claims (
                    run_nonce TEXT PRIMARY KEY,
                    campaign_digest TEXT NOT NULL,
                    corpus_digest TEXT NOT NULL,
                    claimed_at TEXT NOT NULL
                )"""
            )

    def claim(self, *, run_nonce: str, campaign_digest: str, corpus_digest: str) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (run_nonce, campaign_digest, corpus_digest)):
            raise ValueError("nonce and campaign lineage are required")
        try:
            with sqlite3.connect(self.database_path, timeout=10) as connection:
                connection.execute(
                    "INSERT INTO sealed_arena_nonce_claims VALUES (?, ?, ?, ?)",
                    (run_nonce, campaign_digest, corpus_digest, datetime.now(timezone.utc).isoformat()),
                )
        except sqlite3.IntegrityError as exc:
            raise ReplayRejected("sealed Arena run nonce has already been used") from exc


__all__ = ["ReplayRejected", "SealedArenaReplayLedger"]
