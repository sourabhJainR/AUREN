"""Durable, project-neutral AUREN session checkpoints and recovery."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass
class SessionCheckpoint:
    session_id: str
    task_id: str
    project_key: str
    stage: str
    completed_batches: list[str] = field(default_factory=list)
    remaining_batches: list[str] = field(default_factory=list)
    active_provider: str | None = None
    attempt: int = 0
    last_error: str | None = None
    project_root: str | None = None
    intent: str | None = None
    updated_at: float = field(default_factory=time.time)
    state_digest: str = ""

    def seal(self) -> "SessionCheckpoint":
        self.updated_at = time.time()
        payload = asdict(self)
        payload["state_digest"] = ""
        self.state_digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return self


class SessionStore:
    """Atomic JSON checkpoints shared by sessions and projects."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or (Path.home() / ".auren" / "sessions")).expanduser()

    @staticmethod
    def project_key(project_root: Path | str) -> str:
        path = Path(project_root).expanduser().resolve()
        return hashlib.sha256(str(path).encode()).hexdigest()[:20]

    def path(self, session_id: str) -> Path:
        safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_.") or "session"
        return self.root / f"{safe}.json"

    def save(self, checkpoint: SessionCheckpoint) -> Path:
        checkpoint.seal()
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path(checkpoint.session_id)
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(checkpoint), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, target)
        return target

    def load(self, session_id: str) -> SessionCheckpoint | None:
        target = self.path(session_id)
        if not target.is_file():
            return None
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            checkpoint = SessionCheckpoint(**raw)
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not self._valid(checkpoint):
            return None
        return checkpoint

    def recover(self, session_id: str, *, next_stage: str | None = None) -> SessionCheckpoint | None:
        checkpoint = self.load(session_id)
        if checkpoint is None:
            return None
        if next_stage:
            checkpoint.stage = next_stage
        checkpoint.attempt += 1
        checkpoint.last_error = None
        self.save(checkpoint)
        return checkpoint

    @staticmethod
    def _valid(checkpoint: SessionCheckpoint) -> bool:
        digest = checkpoint.state_digest
        checkpoint.state_digest = ""
        current_payload = asdict(checkpoint)
        current_expected = hashlib.sha256(
            json.dumps(current_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if bool(digest) and digest == current_expected:
            checkpoint.state_digest = digest
            return True

        legacy_payload = dict(current_payload)
        legacy_payload.pop("project_root", None)
        legacy_payload.pop("intent", None)
        legacy_expected = hashlib.sha256(
            json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        checkpoint.state_digest = digest
        return bool(digest) and digest == legacy_expected


__all__ = ["SessionCheckpoint", "SessionStore"]
