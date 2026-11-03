"""Bounded private working memory for agents.

Private memory is disposable working state, not team knowledge. Writes are
serialized across threads/processes and old entries are evicted deterministically.
Profile memory adds an immutable profile/revision/digest and run boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class AgentMemory:
    """Bounded agent-private memory; never implicitly promoted to shared learning."""

    def __init__(
        self,
        root: Path,
        agent: str,
        *,
        run_id: str | None = None,
        max_entries: int = 32,
        max_chars: int = 12000,
        record_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError("agent memory max_entries must be a positive integer")
        if type(max_chars) is not int or max_chars < 1:
            raise ValueError("agent memory max_chars must be a positive integer")
        if not isinstance(agent, str) or not agent.strip() or not agent.isprintable():
            raise ValueError("agent memory identity must be non-empty printable text")
        if run_id is not None and (
            not isinstance(run_id, str) or not run_id.strip()
            or len(run_id) > 256 or not run_id.isprintable()
        ):
            raise ValueError("agent memory run_id must be non-empty printable text of at most 256 characters")
        metadata = dict(record_metadata or {})
        try:
            json.dumps(metadata, sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("agent memory record metadata must be JSON-safe") from exc

        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", agent).strip("._") or "agent"
        self.path = Path(root) / ".ai-harness" / "agent-memory" / f"{safe}.jsonl"
        self.lock_path = self.path.with_suffix(".lock")
        self.run_id = run_id
        self.max_entries = max_entries
        self.max_chars = max_chars
        self.record_metadata = metadata
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _snapshot_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if self.run_id is not None and row.get("run_id") != self.run_id:
                continue
            if any(row.get(key) != value for key, value in self.record_metadata.items()):
                continue
            rows.append(row)
        return rows[-self.max_entries:]

    def snapshot(self) -> list[dict[str, Any]]:
        with _lock(self.lock_path):
            return self._snapshot_unlocked()

    def read(self, limit: int = 5000) -> str:
        if type(limit) is not int or limit < 1:
            raise ValueError("agent memory read limit must be a positive integer")
        text = "\n".join(
            str(row.get("text", "")).strip()
            for row in self.snapshot()
            if str(row.get("text", "")).strip()
        )
        return text if len(text) <= limit else text[:max(200, limit - 50)] + "\n...[agent memory compacted]"

    def remember(self, text: str, *, kind: str = "note") -> None:
        clean = str(text).strip()
        if not clean:
            return
        row = {
            "schema_version": 1,
            "kind": str(kind)[:80],
            "text": clean[:2000],
            "run_id": self.run_id,
            "recorded_at": int(time.time()),
            **self.record_metadata,
        }
        with _lock(self.lock_path):
            rows = self._snapshot_unlocked()
            rows.append(row)
            rows = rows[-self.max_entries:]
            while rows and sum(
                len(json.dumps(item, ensure_ascii=False)) + 1 for item in rows
            ) > self.max_chars:
                rows.pop(0)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in rows)
                + ("\n" if rows else ""),
                encoding="utf-8",
            )
            temporary.replace(self.path)


class ProfileAgentMemory(AgentMemory):
    """Private memory partitioned by validated profile revision, agent and run.

    The profile is resolved from the versioned registry. Callers cannot supply a
    namespace or digest. A changed profile digest selects a different memory file,
    and record metadata is checked on every read.
    """

    def __init__(
        self,
        root: Path,
        *,
        profile_id: str,
        profile_revision: int,
        agent_id: str,
        run_id: str,
        max_entries: int = 32,
        max_chars: int = 12000,
        profile_path: Path | None = None,
        organization_registry_path: Path | None = None,
    ) -> None:
        from .agent_profiles import (
            DEFAULT_ORGANIZATION_PATH,
            DEFAULT_PROFILE_PATH,
            resolve_agent_profile,
        )

        if not isinstance(agent_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", agent_id) is None:
            raise ValueError("profile memory agent_id must be a bounded safe identifier")
        if (
            not isinstance(run_id, str) or not run_id.strip()
            or len(run_id) > 256 or not run_id.isprintable()
        ):
            raise ValueError("profile memory requires a non-empty run_id of at most 256 printable characters")

        profile = resolve_agent_profile(
            profile_id,
            revision=profile_revision,
            profile_path=profile_path or DEFAULT_PROFILE_PATH,
            organization_registry_path=organization_registry_path or DEFAULT_ORGANIZATION_PATH,
        )
        profile_key = hashlib.sha256(
            f"{profile.profile_id}:{profile.revision}:{profile.digest}".encode("utf-8")
        ).hexdigest()
        agent_key = hashlib.sha256(agent_id.encode("utf-8")).hexdigest()
        run_key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        memory_key = f"profile-{profile_key}-agent-{agent_key}-run-{run_key}"
        metadata = {
            "profile_id": profile.profile_id,
            "profile_revision": profile.revision,
            "profile_digest": profile.digest,
            "memory_namespace": profile.memory_namespace,
            "agent_id": agent_id,
        }
        super().__init__(
            root,
            memory_key,
            run_id=run_id,
            max_entries=max_entries,
            max_chars=max_chars,
            record_metadata=metadata,
        )
        self.profile_id = profile.profile_id
        self.profile_revision = profile.revision
        self.profile_digest = profile.digest
        self.memory_namespace = profile.memory_namespace
        self.agent_id = agent_id


__all__ = ["AgentMemory", "ProfileAgentMemory"]
