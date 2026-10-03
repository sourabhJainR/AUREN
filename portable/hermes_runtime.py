#!/usr/bin/env python3
"""Provider-neutral agent runtime primitives inspired by modern autonomous agents.

This module deliberately keeps orchestration semantics in AUREN. It provides small,
stdlib-only building blocks for provider routing, profiles, durable sessions,
skills, memory, delegation, tool registration, approvals and scheduled jobs.
External providers and messaging platforms are adapters, not core dependencies.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

RUNTIME_VERSION = "1.0.0"


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    model: str
    endpoint: str | None = None
    capabilities: frozenset[str] = frozenset()
    priority: int = 100
    enabled: bool = True


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any]
    risk: str = "low"
    requires_approval: bool = False
    tags: frozenset[str] = frozenset()


@dataclass
class SessionState:
    session_id: str
    profile: str
    task_id: str | None = None
    status: str = "active"
    turn: int = 0
    summary: str = ""
    state_digest: str = ""
    updated_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str
    tags: frozenset[str] = frozenset()
    version: str = "1.0.0"


class Provider(Protocol):
    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]: ...


class ProviderRouter:
    """Capability-aware model routing with deterministic fallback order."""

    def __init__(self, providers: Iterable[ProviderSpec] = ()) -> None:
        self._providers: dict[str, ProviderSpec] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ProviderSpec) -> None:
        self._providers[provider.name] = provider

    def resolve(self, required: set[str] | None = None, preferred: str | None = None) -> ProviderSpec:
        required = required or set()
        candidates = [p for p in self._providers.values() if p.enabled and required.issubset(p.capabilities)]
        if preferred:
            candidates.sort(key=lambda p: (0 if p.name == preferred else 1, p.priority, p.name))
        else:
            candidates.sort(key=lambda p: (p.priority, p.name))
        if not candidates:
            raise LookupError(f"No enabled provider satisfies capabilities: {sorted(required)}")
        return candidates[0]

    def list(self) -> list[ProviderSpec]:
        return sorted(self._providers.values(), key=lambda p: (p.priority, p.name))


class ToolRegistry:
    """Single registry for local tools, MCP adapters and future gateway tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_.:-]+", tool.name):
            raise ValueError(f"Invalid tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def discover(self, tags: set[str] | None = None) -> list[ToolSpec]:
        tags = tags or set()
        return [t for t in self._tools.values() if tags.issubset(t.tags)]

    def call(self, name: str, approved: bool = False, **kwargs: Any) -> Any:
        tool = self.get(name)
        if tool.requires_approval and not approved:
            raise PermissionError(f"Tool requires approval: {name}")
        return tool.handler(**kwargs)


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def discover(self, query: str = "") -> list[Skill]:
        tokens = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2}
        ranked: list[tuple[int, Skill]] = []
        for skill in self._skills.values():
            haystack = f"{skill.name} {skill.description} {' '.join(skill.tags)}".lower()
            score = sum(1 for token in tokens if token in haystack)
            if score or not tokens:
                ranked.append((score, skill))
        return [skill for _, skill in sorted(ranked, key=lambda x: (-x[0], x[1].name))]


class DurableStore:
    """SQLite state store for sessions, messages, memories and scheduled jobs."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, profile TEXT NOT NULL, task_id TEXT, status TEXT NOT NULL,
                turn INTEGER NOT NULL, summary TEXT NOT NULL, state_digest TEXT NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(content, session_id UNINDEXED, message_id UNINDEXED);
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT, profile TEXT NOT NULL, kind TEXT NOT NULL,
                content TEXT NOT NULL, source TEXT NOT NULL, confidence REAL NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, profile TEXT NOT NULL, expression TEXT NOT NULL, payload TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, next_run REAL, last_run REAL
            );
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def save_session(self, state: SessionState) -> None:
        state.updated_at = time.time()
        state.state_digest = hashlib.sha256(json.dumps(asdict(state), sort_keys=True).encode()).hexdigest()
        self.db.execute(
            "INSERT INTO sessions(id,profile,task_id,status,turn,summary,state_digest,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET profile=excluded.profile,task_id=excluded.task_id,status=excluded.status,turn=excluded.turn,summary=excluded.summary,state_digest=excluded.state_digest,updated_at=excluded.updated_at",
            (state.session_id, state.profile, state.task_id, state.status, state.turn, state.summary, state.state_digest, state.updated_at),
        )
        self.db.commit()

    def append_message(self, session_id: str, role: str, content: str) -> int:
        now = time.time()
        cur = self.db.execute("INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)", (session_id, role, content, now))
        message_id = int(cur.lastrowid)
        self.db.execute("INSERT INTO message_fts(content,session_id,message_id) VALUES(?,?,?)", (content, session_id, message_id))
        self.db.commit()
        return message_id

    def search_sessions(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT session_id, message_id, snippet(message_fts,0,'','', '...', 18) AS snippet FROM message_fts WHERE message_fts MATCH ? LIMIT ?", (query, limit)).fetchall()
        return [dict(row) for row in rows]

    def remember(self, profile: str, kind: str, content: str, source: str, confidence: float = 0.5) -> None:
        confidence = max(0.0, min(1.0, confidence))
        self.db.execute("INSERT INTO memories(profile,kind,content,source,confidence,created_at) VALUES(?,?,?,?,?,?)", (profile, kind, content, source, confidence, time.time()))
        self.db.commit()

    def recall(self, profile: str, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if kind:
            rows = self.db.execute("SELECT * FROM memories WHERE profile=? AND kind=? ORDER BY confidence DESC, created_at DESC LIMIT ?", (profile, kind, limit)).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM memories WHERE profile=? ORDER BY confidence DESC, created_at DESC LIMIT ?", (profile, limit)).fetchall()
        return [dict(row) for row in rows]

    def schedule(self, profile: str, expression: str, payload: dict[str, Any], next_run: float | None = None) -> str:
        job_id = str(uuid.uuid4())
        self.db.execute("INSERT INTO jobs(id,profile,expression,payload,next_run) VALUES(?,?,?,?,?)", (job_id, profile, expression, json.dumps(payload), next_run))
        self.db.commit()
        return job_id


class ApprovalPolicy:
    """Fail-closed policy for commands and high-risk tools."""

    def __init__(self, allowed_commands: Iterable[str] = (), blocked_commands: Iterable[str] = ()) -> None:
        self.allowed = tuple(allowed_commands)
        self.blocked = tuple(blocked_commands)

    def check_command(self, command: str) -> None:
        if any(re.search(pattern, command) for pattern in self.blocked):
            raise PermissionError("Command rejected by security policy")
        if self.allowed and not any(re.search(pattern, command) for pattern in self.allowed):
            raise PermissionError("Command requires explicit approval")


class LocalExecutor:
    def __init__(self, policy: ApprovalPolicy | None = None, cwd: Path | None = None) -> None:
        self.policy = policy or ApprovalPolicy()
        self.cwd = Path(cwd or os.getcwd()).resolve()

    def run(self, command: str, timeout: int = 120, approved: bool = False) -> dict[str, Any]:
        self.policy.check_command(command)
        if self.policy.allowed and not approved:
            raise PermissionError("Command requires explicit approval")
        started = time.time()
        completed = subprocess.run(command, shell=True, cwd=self.cwd, text=True, capture_output=True, timeout=timeout)
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "duration_seconds": round(time.time() - started, 3),
        }


class DelegationCoordinator:
    """Bounded parallel delegation; mutation ownership stays with the caller."""

    def __init__(self, max_workers: int = 4) -> None:
        from concurrent.futures import ThreadPoolExecutor
        self.max_workers = max(1, max_workers)
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="auren-agent")

    def run(self, tasks: Iterable[Callable[[], Any]]) -> list[Any]:
        futures = [self._executor.submit(task) for task in tasks]
        return [future.result() for future in futures]

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)


def capability_matrix() -> dict[str, str]:
    return {
        "multi_provider": "ProviderRouter",
        "profiles": "DurableStore profile-scoped state",
        "persistent_memory": "DurableStore.memories",
        "session_search": "SQLite FTS5",
        "skills": "SkillRegistry",
        "toolsets": "ToolRegistry",
        "approval_and_security": "ApprovalPolicy",
        "local_execution": "LocalExecutor",
        "parallel_delegation": "DelegationCoordinator",
        "scheduled_jobs": "DurableStore.jobs",
        "resumable_sessions": "SessionState + DurableStore",
        "mcp_and_gateway": "adapter boundary; provider/platform specific",
        "multimodal": "adapter boundary; model/provider specific",
        "terminal_backends": "AUREN sandbox/environment adapters",
        "verification_learning": "AUREN harness policies remain authoritative",
    }
