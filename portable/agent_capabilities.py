"""Unified, provider-neutral agent capabilities for AER.

The module is stdlib-only and owns task-facing capability semantics. AER remains
the authority for policy, sandboxing, verification and promotion.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import sqlite3
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

CAPABILITIES = (
    "web_search", "x_search", "terminal", "browser", "file", "vision",
    "image_generation", "tts", "todo", "memory", "session_search",
    "cronjob", "execute_code", "delegate_task", "clarify", "mcp",
    "skills", "background_processes", "local_offload", "provider_fallback",
)

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
_SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*([^\s,;'\"]+)")
_INJECTION = re.compile(r"(?i)ignore\s+(all|previous|prior)\s+instructions")


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: f"{m.group(1)}=<redacted>", text)


def sanitize_untrusted(text: str) -> str:
    if _INJECTION.search(text):
        raise ValueError("untrusted content rejected: prompt-injection pattern")
    return redact(text)


def sanitize_capability_reference(text: str, limit: int = 4096) -> str:
    """Keep optional capability instructions as bounded reference data."""
    clean = redact(str(text))
    clean = _INJECTION.sub("[blocked untrusted instruction]", clean)
    return clean[:max(1, int(limit))]


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    risk: str = "low"
    requires_network: bool = False
    requires_sandbox: bool = False
    fallback: str | None = None


class CapabilityFabric:
    def __init__(self, capabilities: Mapping[str, Capability] | None = None) -> None:
        self._caps = dict(capabilities or self.default_catalog())

    @staticmethod
    def default_catalog() -> dict[str, Capability]:
        return {
            "web_search": Capability("web_search", "web retrieval", requires_network=True),
            "x_search": Capability("x_search", "social search", requires_network=True, fallback="web_search"),
            "terminal": Capability("terminal", "repository command execution", "medium", requires_sandbox=True),
            "browser": Capability("browser", "browser automation", "medium", requires_network=True, fallback="web_search"),
            "file": Capability("file", "repository file access", "medium", requires_sandbox=True),
            "vision": Capability("vision", "image understanding"),
            "image_generation": Capability("image_generation", "image generation", "medium"),
            "tts": Capability("tts", "speech synthesis"),
            "todo": Capability("todo", "dependency-aware task planning"),
            "memory": Capability("memory", "durable project memory"),
            "session_search": Capability("session_search", "cross-session recall"),
            "cronjob": Capability("cronjob", "durable scheduled automation", "medium"),
            "execute_code": Capability("execute_code", "bounded code execution", "high", requires_sandbox=True),
            "delegate_task": Capability("delegate_task", "bounded parallel delegation", "medium"),
            "clarify": Capability("clarify", "structured clarification"),
            "mcp": Capability("mcp", "external tool interoperability", "high"),
            "skills": Capability("skills", "progressive-disclosure procedures"),
            "background_processes": Capability("background_processes", "durable background work", "medium"),
            "local_offload": Capability("local_offload", "bounded local worker execution", "medium", requires_sandbox=True),
            "provider_fallback": Capability("provider_fallback", "provider failover"),
        }

    def discover(self) -> dict[str, Capability]:
        return dict(sorted(self._caps.items()))

    def plan(self, requested: Iterable[str], *, network_allowed: bool, sandbox_available: bool = True,
             max_risk: str = "high") -> list[Capability]:
        if max_risk not in _RISK_ORDER:
            raise ValueError("invalid max_risk")
        result: list[Capability] = []
        seen: set[str] = set()
        for name in requested:
            cap = self._caps.get(name)
            if cap is None:
                raise KeyError(f"unknown capability: {name}")
            if _RISK_ORDER[cap.risk] > _RISK_ORDER[max_risk]:
                raise PermissionError(f"capability exceeds risk budget: {name}")
            if cap.requires_sandbox and not sandbox_available:
                raise PermissionError(f"sandbox required: {name}")
            selected = cap
            if cap.requires_network and not network_allowed:
                if not cap.fallback:
                    raise RuntimeError(f"network required and no safe fallback: {name}")
                selected = self._caps[cap.fallback]
            if selected.name not in seen:
                result.append(selected)
                seen.add(selected.name)
        return result


@dataclass(frozen=True)
class ProviderAdapter:
    name: str
    capabilities: frozenset[str]
    priority: int = 0
    enabled: bool = True


class ProviderAdapterRegistry:
    def __init__(self, adapters: Sequence[ProviderAdapter] = ()) -> None:
        self._adapters = list(adapters)

    def register(self, adapter: ProviderAdapter) -> None:
        self._adapters = [a for a in self._adapters if a.name != adapter.name]
        self._adapters.append(adapter)

    def resolve(self, required: Iterable[str], preferred: Sequence[str] = ()) -> ProviderAdapter:
        required_set = set(required)
        preferred_rank = {name: i for i, name in enumerate(preferred)}
        candidates = [a for a in self._adapters if a.enabled and required_set.issubset(a.capabilities)]
        if not candidates:
            raise LookupError(f"no provider supports: {sorted(required_set)}")
        return sorted(candidates, key=lambda a: (preferred_rank.get(a.name, 10_000), -a.priority, a.name))[0]


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    project: str
    category: str
    text: str
    intent_digest: str | None
    confidence: float
    verified: bool
    created_at: str


class PersistentMemory:
    """Durable SQLite memory with WAL, FTS5 recall, scoping and redaction."""
    def __init__(self, path: Path | str, *, max_chars: int = 100_000,
                 require_approval: bool = True, require_write_approval: bool | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_chars = max_chars
        if require_write_approval is not None:
            require_approval = require_write_approval
        self.require_approval = require_approval
        self._lock = threading.RLock()
        self._fts_available = True
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS memory(
                id TEXT PRIMARY KEY, project TEXT NOT NULL, category TEXT NOT NULL,
                text TEXT NOT NULL, intent_digest TEXT, confidence REAL NOT NULL,
                verified INTEGER NOT NULL, created_at TEXT NOT NULL)""")
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, text, category, project)")
            except sqlite3.OperationalError:
                self._fts_available = False
            db.execute("CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory(project, intent_digest, created_at)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def remember(self, project: str, category: str, text: str | None = None, *,
                 intent_digest: str | None = None, confidence: float = 0.0,
                 verified: bool = False, approved: bool = False) -> MemoryRecord | None:
        if text is None:
            text, category, project = category, project, "default"
        if self.require_approval and not approved:
            return None
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        clean = sanitize_untrusted(text)
        with self._lock, self._connect() as db:
            used = db.execute("SELECT COALESCE(SUM(length(text)),0) FROM memory WHERE project=?", (project,)).fetchone()[0]
            if used + len(clean) > self.max_chars:
                raise ValueError("memory budget exceeded; consolidate or remove stale memories")
            duplicate = db.execute(
                "SELECT id,project,category,text,intent_digest,confidence,verified,created_at FROM memory "
                "WHERE project=? AND category=? AND text=? AND COALESCE(intent_digest,'')=COALESCE(?, '')",
                (project, category, clean, intent_digest),
            ).fetchone()
            if duplicate:
                return self._record(duplicate)
            record = MemoryRecord(uuid.uuid4().hex, project, category, clean, intent_digest, confidence, verified, _utc().isoformat())
            db.execute("INSERT INTO memory VALUES(?,?,?,?,?,?,?,?)", (record.id, record.project, record.category, record.text, record.intent_digest, record.confidence, int(record.verified), record.created_at))
            if self._fts_available:
                db.execute("INSERT INTO memory_fts(id,text,category,project) VALUES(?,?,?,?)", (record.id, record.text, record.category, record.project))
            return record

    @staticmethod
    def _record(row: Sequence[Any]) -> MemoryRecord:
        return MemoryRecord(row[0], row[1], row[2], row[3], row[4], float(row[5]), bool(row[6]), row[7])

    def search(self, project: str, query: str | None = None, *, intent_digest: str | None = None,
               limit: int = 20) -> list[MemoryRecord]:
        if query is None:
            query, project = project, "default"
        if not query.strip():
            return []
        terms = " ".join(re.findall(r"[A-Za-z0-9_]+", query))
        if not terms:
            return []
        with self._lock, self._connect() as db:
            params: tuple[Any, ...]
            if self._fts_available:
                rows = db.execute("""SELECT m.id,m.project,m.category,m.text,m.intent_digest,m.confidence,m.verified,m.created_at
                    FROM memory AS m JOIN memory_fts AS f ON f.id=m.id
                    WHERE m.project=? AND memory_fts MATCH ? AND (? IS NULL OR m.intent_digest=?)
                    ORDER BY m.verified DESC,m.confidence DESC,m.created_at DESC LIMIT ?""",
                    (project, terms, intent_digest, intent_digest, limit)).fetchall()
            else:
                like = "%" + terms.replace(" ", "%") + "%"
                params = (project, like, intent_digest, intent_digest, limit)
                rows = db.execute("""SELECT id,project,category,text,intent_digest,confidence,verified,created_at
                    FROM memory WHERE project=? AND text LIKE ? AND (? IS NULL OR intent_digest=?)
                    ORDER BY verified DESC,confidence DESC,created_at DESC LIMIT ?""", params).fetchall()
        return [self._record(r) for r in rows]

    def close(self) -> None:
        return None


@dataclass(frozen=True)
class DelegationReceipt:
    id: str
    task_id: str
    status: str
    started_at: str
    completed_at: str | None
    result: str | None
    error: str | None


class DelegationPool:
    def __init__(self, max_workers: int = 4) -> None:
        if not 1 <= max_workers <= 16:
            raise ValueError("max_workers must be 1..16")
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aer-agent")

    def submit(self, task_id: str, fn: Callable[[], Any]) -> tuple[DelegationReceipt, Future[Any]]:
        receipt = DelegationReceipt(uuid.uuid4().hex, task_id, "running", _utc().isoformat(), None, None, None)
        return receipt, self._pool.submit(fn)

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)


@dataclass(frozen=True)
class Schedule:
    id: str
    task: str
    interval_seconds: int
    max_attempts: int
    next_run: str
    enabled: bool
    attempts: int


class AutomationScheduler:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True); self._lock = threading.RLock()
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""CREATE TABLE IF NOT EXISTS schedules(
              id TEXT PRIMARY KEY,task TEXT NOT NULL,interval_seconds INTEGER NOT NULL,
              max_attempts INTEGER NOT NULL,next_run TEXT NOT NULL,enabled INTEGER NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,claim TEXT);
              CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,schedule_id TEXT NOT NULL,
              started_at TEXT NOT NULL,finished_at TEXT,status TEXT,detail TEXT);""")

    def add(self, task: str, interval_seconds: int, *, max_attempts: int = 3, start: datetime | None = None) -> Schedule:
        if interval_seconds < 1 or max_attempts < 1:
            raise ValueError("interval_seconds and max_attempts must be positive")
        when = start or _utc(); sid = uuid.uuid4().hex; clean = sanitize_untrusted(task)
        with self._lock, sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO schedules VALUES(?,?,?,?,?,?,?,NULL)", (sid, clean, interval_seconds, max_attempts, when.isoformat(), 1, 0))
        return Schedule(sid, task, interval_seconds, max_attempts, when.isoformat(), True, 0)

    def due(self, now: datetime | None = None) -> list[Schedule]:
        now = now or _utc()
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT id,task,interval_seconds,max_attempts,next_run,enabled,attempts FROM schedules WHERE enabled=1 AND claim IS NULL AND next_run<=? ORDER BY next_run,id", (now.isoformat(),)).fetchall()
        return [Schedule(r[0], r[1], int(r[2]), int(r[3]), r[4], bool(r[5]), int(r[6])) for r in rows]

    def claim(self, schedule_id: str, *, now: datetime | None = None) -> str | None:
        now = now or _utc(); claim = uuid.uuid4().hex
        with self._lock, sqlite3.connect(self.path) as db:
            row = db.execute("SELECT next_run,enabled,attempts,max_attempts,claim FROM schedules WHERE id=?", (schedule_id,)).fetchone()
            if not row or not row[1] or row[4] or datetime.fromisoformat(row[0]) > now or int(row[2]) >= int(row[3]):
                return None
            updated = db.execute("UPDATE schedules SET attempts=attempts+1,claim=? WHERE id=? AND claim IS NULL AND enabled=1", (claim, schedule_id)).rowcount
            return claim if updated == 1 else None

    def finish(self, schedule_id: str, claim: str, status: str, detail: str = "", *, now: datetime | None = None) -> None:
        if status not in {"success", "retryable", "failed", "cancelled"}:
            raise ValueError("invalid run status")
        now = now or _utc()
        with self._lock, sqlite3.connect(self.path) as db:
            row = db.execute("SELECT interval_seconds,max_attempts,attempts FROM schedules WHERE id=? AND claim=?", (schedule_id, claim)).fetchone()
            if not row:
                raise KeyError("invalid scheduler claim")
            exhausted = int(row[2]) >= int(row[1])
            enabled = int(status == "success" or (status == "retryable" and not exhausted))
            next_run = now + timedelta(seconds=int(row[0]))
            db.execute("UPDATE schedules SET claim=NULL,enabled=?,next_run=?,attempts=? WHERE id=?", (enabled, next_run.isoformat(), 0 if status == "success" else int(row[2]), schedule_id))
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", (uuid.uuid4().hex, schedule_id, now.isoformat(), now.isoformat(), status, redact(detail)))

    def recent_runs(self, schedule_id: str, limit: int = 20) -> list[dict[str, str | None]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT id,schedule_id,started_at,finished_at,status,detail FROM runs WHERE schedule_id=? ORDER BY finished_at DESC LIMIT ?", (schedule_id, limit)).fetchall()
        return [dict(id=r[0], schedule_id=r[1], started_at=r[2], finished_at=r[3], status=r[4], detail=r[5]) for r in rows]

    def close(self) -> None:
        return None


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str
    requires: frozenset[str] = frozenset()


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if not skill.name.strip() or not skill.instructions.strip():
            raise ValueError("skill name and instructions are required")
        self._skills[skill.name] = skill

    def discover(self, query: str = "") -> list[Skill]:
        tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
        def score(s: Skill) -> tuple[int, str]:
            words = set(re.findall(r"[a-z0-9]+", (s.name + " " + s.description).lower()))
            return (len(tokens & words), s.name)
        return sorted(self._skills.values(), key=score, reverse=True)

    def load(self, name: str, available: Iterable[str]) -> Skill:
        skill = self._skills[name]
        missing = skill.requires - set(available)
        if missing:
            raise PermissionError(f"skill prerequisites unavailable: {sorted(missing)}")
        return skill


@dataclass(frozen=True)
class CapabilityOption:
    """Runtime-discovered execution option with optional skill metadata."""
    name: str
    source: str = "core"
    description: str = ""
    instructions: str = ""
    tags: frozenset[str] = frozenset()
    phase: str = "general"
    provides: frozenset[str] = frozenset()
    requires: frozenset[str] = frozenset()
    model_invocable: bool = True
    available: bool = True
    risk: str = "low"
    requires_network: bool = False
    requires_sandbox: bool = False
    estimated_latency_ms: float = 250.0
    estimated_cost: float = 0.5
    evidence_quality: float = 0.5
    historical_success: float = 0.5
    confidence: float = 0.25
    resource_demand: float = 0.25
    fallback: str | None = None


@dataclass(frozen=True)
class CapabilityDecision:
    selected: str
    source: str
    score: float
    confidence: float
    rationale: str
    alternatives: tuple[str, ...] = ()
    degraded: bool = False
    selected_set: tuple[str, ...] = ()
    bundle_id: str = ""
    bundle_score: float = 0.0
    bundle_confidence: float = 0.0
    bundle_status: str = "experimental"
    bundle_cost: float = 0.0
    bundle_latency_ms: float = 0.0
    execution_groups: tuple[tuple[str, ...], ...] = ()


class CapabilityExecutioner:
    """Select the smallest safe capability set for the current execution.

    External skills/plugins/MCP/providers are optional discovery inputs. They
    cannot bypass AER's risk, sandbox, network, failure and stopping policy.
    The selector is deterministic when history is absent and bounded when
    history is present, preventing one successful path from becoming a hard
    dependency.
    """

    def __init__(
        self,
        *,
        discoverers: Iterable[Callable[[], Iterable[CapabilityOption]]] = (),
        min_exploration: float = 0.15,
    ) -> None:
        self._discoverers = tuple(discoverers)
        self.min_exploration = max(0.0, min(0.5, float(min_exploration)))

    def discover(
        self,
        *,
        core: Iterable[CapabilityOption] = (),
        skills: SkillRegistry | None = None,
        query: str = "",
    ) -> tuple[CapabilityOption, ...]:
        options: dict[str, CapabilityOption] = {}
        for option in core:
            if option.available:
                options[option.name] = option
        if skills is not None:
            for skill in skills.discover(query):
                options.setdefault(
                    "skill:" + skill.name,
                    CapabilityOption(
                        name="skill:" + skill.name,
                        source="skill",
                        description=skill.description,
                        tags=frozenset(re.findall(r"[a-z0-9]+", (skill.name + " " + skill.description).lower())),
                    ),
                )
        for discoverer in self._discoverers:
            try:
                for option in discoverer() or ():
                    if isinstance(option, CapabilityOption) and option.available:
                        options.setdefault(option.name, option)
            except (OSError, RuntimeError, ValueError, TypeError):
                # Optional integrations must degrade to the core catalog.
                continue
        return tuple(sorted(options.values(), key=lambda item: (item.source, item.name)))


    def discover_installed(self, project_root: Path | str | None = None) -> tuple[CapabilityOption, ...]:
        """Discover optional skills and host-advertised integrations without imports.

        Skills are discovered from conventional directories and only their
        front matter/first bounded description is read. MCP/plugin capability
        descriptors may be supplied as JSON through AER_MCP_CAPABILITIES and
        AER_PLUGIN_CAPABILITIES. Invalid or missing sources are ignored.
        """
        root = Path(project_root or ".").expanduser().resolve()
        options: list[CapabilityOption] = []
        skill_roots = [
            root / ".claude" / "skills",
            root / ".ai-harness" / "skills",
            root / "skills",
        ]
        configured = os.environ.get("AER_SKILLS_PATH", "")
        if configured:
            skill_roots.extend(Path(item).expanduser() for item in configured.split(os.pathsep) if item.strip())
        seen: set[str] = set()
        for skill_root in skill_roots:
            if not skill_root.is_dir():
                continue
            try:
                entries = sorted(skill_root.iterdir(), key=lambda item: item.name)
            except OSError:
                continue
            for directory in entries:
                if not directory.is_dir() or directory.name in seen:
                    continue
                skill_file = directory / "SKILL.md"
                if not skill_file.is_file():
                    continue
                try:
                    raw = skill_file.read_text(encoding="utf-8", errors="replace")[:4096]
                except OSError:
                    continue
                metadata, body = self._skill_metadata(raw)
                codex_model_invocable = self._codex_model_invocable(directory)
                description = str(metadata.get("description") or next(
                    (line.lstrip("# ").strip() for line in body.splitlines() if line.strip() and not line.startswith("---")),
                    directory.name,
                ))[:512]
                name = "skill:" + directory.name
                tags = metadata.get("tags") or re.findall(r"[a-z0-9]+", (directory.name + " " + description).lower())
                provides = metadata.get("provides", ())
                requires = metadata.get("requires", ())
                options.append(CapabilityOption(
                    name=name,
                    source="skill",
                    description=description,
                    instructions=sanitize_capability_reference(raw),
                    tags=frozenset(str(item).lower() for item in tags if str(item).strip()),
                    phase=str(metadata.get("phase") or "general").strip().lower(),
                    provides=frozenset(str(item).lower() for item in provides if str(item).strip()),
                    requires=frozenset(str(item).lower() for item in requires if str(item).strip()),
                    model_invocable=bool(metadata.get("model_invocable", True)) and codex_model_invocable,
                    risk=str(metadata.get("risk", "low")).strip().lower(),
                    requires_network=bool(metadata.get("requires_network", False)),
                    requires_sandbox=bool(metadata.get("requires_sandbox", False)),
                ))
                seen.add(directory.name)
        for source, payload in (
            ("mcp", os.environ.get("AER_MCP_CAPABILITIES", "")),
            ("plugin", os.environ.get("AER_PLUGIN_CAPABILITIES", "")),
        ):
            try:
                options.extend(self._host_options(source, payload))
            except (TypeError, ValueError, OverflowError):
                continue
        return tuple(sorted({item.name: item for item in options}.values(), key=lambda item: (item.source, item.name)))

    @staticmethod
    def _codex_model_invocable(skill_root: Path) -> bool:
        """Honor the Codex sidecar when a skill declares implicit invocation policy."""
        sidecar = skill_root / "agents" / "openai.yaml"
        try:
            raw = sidecar.read_text(encoding="utf-8", errors="replace")[:2048]
        except OSError:
            return True
        match = re.search(r"(?mi)^\s*allow_implicit_invocation\s*:\s*(true|false)\s*$", raw)
        return not (match and match.group(1).lower() == "false")

    @staticmethod
    def _skill_metadata(raw: str) -> tuple[dict[str, Any], str]:
        """Parse bounded skill front matter without adding a YAML dependency."""
        lines = raw.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, raw
        end = next((i for i in range(1, min(len(lines), 80)) if lines[i].strip() == "---"), None)
        if end is None:
            return {}, raw
        metadata: dict[str, Any] = {}
        list_keys = {"tags", "provides", "requires"}
        for line in lines[1:end]:
            if not line.strip() or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower().replace("-", "_")
            value = value.strip()
            if key in list_keys:
                if value.startswith("[") and value.endswith("]"):
                    metadata[key] = [item.strip().strip("'").strip('"') for item in value[1:-1].split(",") if item.strip()]
                elif value:
                    metadata[key] = [item.strip() for item in value.split(",") if item.strip()]
            elif key in {"model_invocable", "disable_model_invocation", "requires_network", "requires_sandbox"}:
                parsed = value.lower() not in {"false", "0", "no", "off"}
                metadata["model_invocable" if key == "model_invocable" else key] = parsed
                if key == "disable_model_invocation":
                    metadata["model_invocable"] = not parsed
            elif key in {"description", "phase", "risk"}:
                metadata[key] = value.strip("'").strip('"')
        return metadata, "\n".join(lines[end + 1:])

    @staticmethod
    def _host_options(source: str, payload: str) -> tuple[CapabilityOption, ...]:
        if not payload.strip():
            return ()
        try:
            rows = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
        if not isinstance(rows, list):
            return ()
        options: list[CapabilityOption] = []
        for row in rows[:64]:
            if not isinstance(row, Mapping) or not str(row.get("name", "")).strip():
                continue
            try:
                tags = (
                    frozenset(str(item).lower() for item in row.get("tags", ()) if str(item).strip())
                    if isinstance(row.get("tags", ()), (list, tuple, set))
                    else frozenset()
                )
                options.append(CapabilityOption(
                    name=str(row["name"]).strip(),
                    source=source,
                    description=str(row.get("description", ""))[:512],
                    instructions=sanitize_capability_reference(row.get("instructions", "")),
                    tags=tags,
                    phase=str(row.get("phase", "general")).strip().lower(),
                    provides=frozenset(str(item).lower() for item in row.get("provides", ()) if str(item).strip()) if isinstance(row.get("provides", ()), (list, tuple, set)) else frozenset(),
                    requires=frozenset(str(item).lower() for item in row.get("requires", ()) if str(item).strip()) if isinstance(row.get("requires", ()), (list, tuple, set)) else frozenset(),
                    model_invocable=bool(row.get("model_invocable", True)),
                    risk=str(row.get("risk", "low")),
                    requires_network=bool(row.get("requires_network", False)),
                    requires_sandbox=bool(row.get("requires_sandbox", False)),
                    estimated_latency_ms=max(0.0, float(row.get("estimated_latency_ms", 250.0))),
                    estimated_cost=max(0.0, min(1.0, float(row.get("estimated_cost", 0.5)))),
                    evidence_quality=max(0.0, min(1.0, float(row.get("evidence_quality", 0.5)))),
                    historical_success=max(0.0, min(1.0, float(row.get("historical_success", 0.5)))),
                    confidence=max(0.0, min(1.0, float(row.get("confidence", 0.25)))),
                    resource_demand=max(0.0, min(1.0, float(row.get("resource_demand", 0.25)))),
                    fallback=str(row.get("fallback")) if row.get("fallback") else None,
                ))
            except (TypeError, ValueError, OverflowError):
                # One bad optional descriptor must not hide valid siblings.
                continue
        return tuple(options)


    @staticmethod
    def candidate_portfolio(
        options: Iterable[CapabilityOption],
        *,
        request: str = "",
        max_candidates: int = 32,
    ) -> tuple[CapabilityOption, ...]:
        """Bound discovery while preserving safe fallback and source diversity."""
        limit = max(1, min(int(max_candidates), 64))
        candidates = list({option.name: option for option in options if option.available}.values())
        if len(candidates) <= limit:
            return tuple(sorted(candidates, key=lambda item: (item.source, item.name)))
        tokens = set(re.findall(r"[a-z0-9]+", request.lower()))

        def fit(option: CapabilityOption) -> tuple[float, float, float, float, str]:
            words = set(re.findall(
                r"[a-z0-9]+",
                (option.name + " " + option.description + " " + " ".join(option.tags)).lower(),
            ))
            overlap = len(tokens & words)
            quality = max(0.0, min(1.0, option.evidence_quality))
            confidence = max(0.0, min(1.0, option.confidence))
            cost = max(0.0, min(1.0, option.estimated_cost))
            return (float(overlap), quality, confidence, -cost, option.name)

        by_source: dict[str, list[CapabilityOption]] = {}
        for option in candidates:
            by_source.setdefault(option.source, []).append(option)
        sources = sorted(by_source)
        selected: list[CapabilityOption] = []
        # Reserve one representative per discovered source whenever the budget
        # permits. This prevents a large optional ecosystem from hiding another.
        if len(sources) <= limit:
            for source in sources:
                selected.append(max(by_source[source], key=fit))
        # Prefer remaining core options, then fill the residual budget by the
        # same evidence-aware fit. Selection is deterministic.
        remaining = [o for o in candidates if o not in selected]
        remaining.sort(key=lambda option: (
            option.source != "core",
            -fit(option)[0],
            -fit(option)[1],
            -fit(option)[2],
            fit(option)[3],
            option.name,
        ))
        selected.extend(remaining[:max(0, limit - len(selected))])
        return tuple(sorted(selected[:limit], key=lambda item: (item.source, item.name)))


    def select(
        self,
        *,
        request: str,
        options: Iterable[CapabilityOption],
        required: Iterable[str] = (),
        failed: Iterable[str] = (),
        network_allowed: bool = True,
        sandbox_available: bool = True,
        max_risk: str = "high",
        resource_budget: float = 1.0,
        history: Mapping[str, Mapping[str, float]] | None = None,
    ) -> CapabilityDecision:
        if max_risk not in _RISK_ORDER:
            raise ValueError("invalid max_risk")
        failed_set = set(failed)
        required_set = set(required)
        tokens = set(re.findall(r"[a-z0-9]+", request.lower()))
        history = history or {}
        eligible = [
            option for option in options
            if option.name not in failed_set
            and option.available
            and self._allowed(option, network_allowed, sandbox_available, max_risk)
        ]
        # Bounded exploration is driven by evidence scarcity, not only whether
        # a capability has ever been observed. This lets under-observed safe paths
        # receive a small trial bonus while proven paths naturally move into
        # exploitation as confidence and sample count rise.
        exploration_candidates = [
            option for option in eligible
            if self._exploration_need(history.get(option.name, {})) > 0.0
        ]
        exploration = max(
            exploration_candidates,
            key=lambda option: (
                self._exploration_need(history.get(option.name, {})),
                -_RISK_ORDER.get(option.risk, 99),
                -option.evidence_quality,
                option.name,
            ),
        ) if exploration_candidates and self.min_exploration > 0 else None
        candidates: list[tuple[CapabilityOption, float]] = []
        for option in options:
            if option.name in failed_set or not option.available:
                continue
            if self._allowed(option, network_allowed, sandbox_available, max_risk):
                overlap = len(tokens & set(option.tags | frozenset(re.findall(r"[a-z0-9]+", option.description.lower()))))
                fit = min(1.0, overlap / max(1, min(5, len(tokens)))) if tokens else 0.25
                prior = history.get(option.name, {}) if history else {}
                success = max(0.0, min(1.0, float(prior.get("success_rate", option.historical_success))))
                evidence = max(0.0, min(1.0, float(prior.get("evidence_quality", option.evidence_quality))))
                confidence = max(0.0, min(1.0, float(prior.get("confidence", option.confidence))))
                learned_cost = max(0.0, min(1.0, float(prior.get("avg_cost", option.estimated_cost))))
                learned_latency = max(0.0, float(prior.get("avg_latency", option.estimated_latency_ms / 1000.0)))
                failure = max(0.0, min(1.0, float(prior.get("failure_rate", 1.0 - success))))
                # Keep history influential but bounded; no single historical win
                # can turn an optional provider into a mandatory dependency.
                history_weight = min(0.45, max(0.0, confidence) * 0.45)
                learned = (1.0 - history_weight) * option.historical_success + history_weight * success
                resource = max(0.0, min(1.0, resource_budget))
                pressure = max(0.0, min(1.0, option.resource_demand / max(resource, 0.01)))
                score = (
                    0.30 * fit
                    + 0.25 * learned
                    + 0.18 * max(0.0, min(1.0, evidence))
                    + 0.10 * max(0.0, min(1.0, option.confidence))
                    + 0.10 * max(0.0, min(1.0, option.confidence + self.min_exploration))
                    - 0.04 * max(0.0, min(1.0, learned_latency / 5.0))
                    - 0.08 * learned_cost
                    - 0.10 * pressure
                    - 0.08 * failure
                )
                candidates.append((option, score))
        if not candidates:
            raise LookupError("no safe capability available for request")
        if exploration is not None:
            exploration_need = self._exploration_need(history.get(exploration.name, {}))
            exploration_bonus = min(0.03, self.min_exploration * exploration_need)
            for index, (option, score) in enumerate(candidates):
                if option.name == exploration.name:
                    candidates[index] = (option, score + exploration_bonus)
                    break
        candidates.sort(key=lambda item: (-item[1], item[0].name))
        selected, score = candidates[0]
        degraded = selected.source != "core" and not any(option.source == "core" for option, _ in candidates)
        if required_set and not required_set.issubset({selected.name}):
            missing = sorted(required_set - {selected.name})
            raise PermissionError(f"required capabilities not selected: {missing}")
        alternatives = tuple(item.name for item, _ in candidates[1:4])
        confidence = max(0.0, min(1.0, 0.45 + 0.45 * selected.confidence + 0.10 * selected.historical_success))
        rationale = (
            f"selected {selected.name} from {selected.source}; "
            f"score={score:.3f}; candidates={len(candidates)}; "
            f"fallback remains available={bool(alternatives)}"
        )
        return CapabilityDecision(selected.name, selected.source, score, confidence, rationale, alternatives, degraded)

    @staticmethod
    def bundle_id(options: Iterable[CapabilityOption]) -> str:
        members = sorted(f"{option.source}:{option.name}" for option in options)
        return hashlib.sha256("|".join(members).encode()).hexdigest()[:16]

    @staticmethod
    def evidence_value(
        option: CapabilityOption,
        *,
        history: Mapping[str, Mapping[str, float]] | None = None,
    ) -> float:
        """Estimate whether a selected skill is worth its execution overhead."""
        observed = (history or {}).get(option.name, {})
        evidence = float(observed.get("evidence_quality", 0.5))
        confidence = float(observed.get("confidence", 0.0))
        success = float(observed.get("success_rate", 0.5))
        failure = float(observed.get("failure_rate", 0.5))
        return max(0.0, min(1.0, 0.45 * evidence + 0.25 * confidence + 0.20 * success - 0.10 * failure))

    @staticmethod
    def execution_schedule(
        members: Sequence[CapabilityOption],
        *,
        max_parallel: int = 3,
    ) -> tuple[tuple[str, ...], ...]:
        """Return bounded parallel-ready groups for the selected capabilities."""
        groups = CapabilityExecutioner._execution_groups(members)
        limit = max(1, min(int(max_parallel), 3))
        return tuple(
            tuple(group[index:index + limit])
            for group in groups
            for index in range(0, len(group), limit)
        )

    @staticmethod
    def _execution_groups(members: Sequence[CapabilityOption]) -> tuple[tuple[str, ...], ...]:
        """Build deterministic phase/dependency groups for a selected bundle."""
        by_name = {option.name: option for option in members}
        rank = {
            "research": 10, "discovery": 10, "planning": 20, "plan": 20,
            "implementation": 30, "implementation-review": 40,
            "verification": 50, "review": 50, "testing": 50,
            "synthesis": 60, "handoff": 70, "general": 25,
        }
        edges: dict[str, set[str]] = {name: set() for name in by_name}
        for option in members:
            for requirement in option.requires:
                providers = [
                    candidate.name for candidate in members
                    if candidate.name != option.name and (
                        requirement in candidate.provides
                        or requirement in candidate.tags
                        or requirement == candidate.name
                    )
                ]
                for provider in providers:
                    edges[option.name].add(provider)
        remaining = set(by_name)
        groups: list[tuple[str, ...]] = []
        while remaining:
            ready = [
                name for name in remaining
                if not (edges[name] & remaining)
            ]
            if not ready:
                # Cyclic metadata must never deadlock execution. Fall back to
                # deterministic phase/name order and keep the cycle visible.
                ready = [min(remaining, key=lambda name: (rank.get(by_name[name].phase, 25), name))]
            phase = min(rank.get(by_name[name].phase, 25) for name in ready)
            current = tuple(sorted(
                name for name in ready
                if rank.get(by_name[name].phase, 25) == phase
            ))
            groups.append(current)
            remaining.difference_update(current)
        return tuple(groups)

    @staticmethod
    def _bundle_status(prior: Mapping[str, float]) -> str:
        samples = int(float(prior.get("samples", 0)))
        success = float(prior.get("success_rate", 0.5))
        failure = float(prior.get("failure_rate", 0.5))
        if samples >= 6 and failure >= 0.75:
            return "retired"
        if samples >= 3 and (failure >= 0.55 or success < 0.5):
            return "degraded"
        if samples >= 4 and success >= 0.78 and float(prior.get("confidence", 0.0)) >= 0.5:
            delta = prior.get("collaboration_delta")
            if delta is not None and float(delta) >= 0.05:
                return "proven"
        return "experimental"

    @staticmethod
    def _bundle_metrics(
        request: str,
        members: Sequence[CapabilityOption],
        history: Mapping[str, Mapping[str, float]],
    ) -> tuple[float, float, float, float, float, float, str]:
        tokens = set(re.findall(r"[a-z0-9]+", request.lower()))
        covered = set(tokens)
        provided = set(tokens)
        phases: set[str] = set()
        sources: set[str] = set()
        redundancy: list[float] = []
        total_cost = 0.0
        total_latency = 0.0
        evidence: list[float] = []
        confidence: list[float] = []
        for option in members:
            phases.add(option.phase)
            sources.add(option.source)
            option_tokens = set(re.findall(r"[a-z0-9]+", (option.name + " " + option.description).lower())) | set(option.tags)
            covered.update(option_tokens)
            provided.update(option_tokens | set(option.provides))
            prior = history.get(option.name, {})
            evidence.append(max(0.0, min(1.0, float(prior.get("evidence_quality", option.evidence_quality)))))
            confidence.append(max(0.0, min(1.0, float(prior.get("confidence", option.confidence)))))
            total_cost += max(0.0, min(1.0, float(prior.get("avg_cost", option.estimated_cost))))
            total_latency += max(0.0, float(prior.get("avg_latency", option.estimated_latency_ms / 1000.0)))
        for left, right in itertools.combinations(members, 2):
            ltags = set(left.tags) | set(re.findall(r"[a-z0-9]+", left.description.lower()))
            rtags = set(right.tags) | set(re.findall(r"[a-z0-9]+", right.description.lower()))
            union = ltags | rtags
            redundancy.append(len(ltags & rtags) / max(1, len(union)))
        missing = sorted({
            requirement
            for option in members
            for requirement in option.requires
            if requirement not in provided
        })
        if missing:
            return -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, "missing_requirements=" + ",".join(missing)
        coverage = min(1.0, len(covered) / max(1, len(tokens)))
        phase_diversity = len(phases) / max(1, len(members))
        source_diversity = len(sources) / max(1, len(members))
        avg_evidence = sum(evidence) / max(1, len(evidence))
        avg_confidence = sum(confidence) / max(1, len(confidence))
        redundancy_score = sum(redundancy) / max(1, len(redundancy))
        return (
            coverage,
            phase_diversity,
            source_diversity,
            avg_evidence,
            avg_confidence,
            redundancy_score,
            "",
        )

    def select_collaborative(
        self,
        *,
        request: str,
        options: Iterable[CapabilityOption],
        required: Iterable[str] = (),
        failed: Iterable[str] = (),
        network_allowed: bool = True,
        sandbox_available: bool = True,
        max_risk: str = "high",
        resource_budget: float = 1.0,
        history: Mapping[str, Mapping[str, float]] | None = None,
        bundle_history: Mapping[str, Mapping[str, float]] | None = None,
        contribution_history: Mapping[str, Mapping[str, float]] | None = None,
        max_skills: int = 3,
    ) -> CapabilityDecision:
        """Select and evaluate a bounded skill bundle.

        The selector evaluates singleton, pair and triple bundles from a small
        candidate portfolio. Historical bundle outcomes can graduate useful
        combinations and retire repeatedly failing ones. Policy is checked for
        every member before scoring.
        """
        options = tuple(options)
        history = history or {}
        bundle_history = bundle_history or {}
        contribution_history = contribution_history or {}
        limit = max(1, min(3, int(max_skills)))
        primary = self.select(
            request=request, options=options, required=required, failed=failed,
            network_allowed=network_allowed, sandbox_available=sandbox_available,
            max_risk=max_risk, resource_budget=resource_budget, history=history,
        )
        eligible = [
            option for option in options
            if option.name not in set(failed)
            and option.available
            and self._allowed(option, network_allowed, sandbox_available, max_risk)
        ]
        request_tokens = set(re.findall(r"[a-z0-9]+", request.lower()))
        eligible.sort(
            key=lambda option: (
                len(request_tokens & (set(option.tags) | set(re.findall(r"[a-z0-9]+", option.description.lower())))),
                option.evidence_quality,
                option.confidence,
                -option.estimated_cost,
                option.name,
            ),
            reverse=True,
        )
        pool_names = {primary.selected}
        pool_names.update(option.name for option in eligible[:11])
        pool = tuple(option for option in eligible if option.name in pool_names)
        by_name = {option.name: option for option in pool}
        candidates: list[tuple[float, tuple[CapabilityOption, ...], float, float, float, float, str, float]] = []
        for size in range(1, limit + 1):
            for names in itertools.combinations(sorted(by_name), size):
                members = tuple(by_name[name] for name in names)
                 metrics = self._bundle_metrics(request, members, history)
                coverage, phase_diversity, source_diversity, evidence, confidence, redundancy, reason = metrics
                if coverage < 0 or reason:
                    continue
                fingerprint = self.bundle_id(members)
                prior = bundle_history.get(fingerprint, {})
                status = self._bundle_status(prior)
                if status == "retired":
                    continue
                # A repeatedly measured negative collaboration delta is a
                # counterfactual rejection signal: do not keep paying to rediscover
                # a bundle that underperforms its singleton baseline.
                if int(float(prior.get("samples", 0))) >= 3 and prior.get("collaboration_delta") is not None:
                    if float(prior.get("collaboration_delta", 0.0)) < 0.0:
                        continue
                bundle_success = max(0.0, min(1.0, float(prior.get("success_rate", 0.5))))
                bundle_evidence = max(0.0, min(1.0, float(prior.get("evidence_quality", evidence))))
                bundle_confidence = max(0.0, min(1.0, float(prior.get("confidence", confidence))))
                cost = min(1.0, sum(max(0.0, min(1.0, float(history.get(o.name, {}).get("avg_cost", o.estimated_cost)))) for o in members))
                latency = sum(max(0.0, float(history.get(o.name, {}).get("avg_latency", o.estimated_latency_ms / 1000.0))) for o in members)
                member_contribution = sum(
                    max(0.0, min(1.0, float(contribution_history.get(o.name, {}).get("evidence_quality", 0.5))))
                    for o in members
                ) / max(1, len(members))
                normalized_latency = min(1.0, latency / 5.0)
                growth = 0.04 if size > 1 and phase_diversity > 0.5 else 0.0
                score = (
                    0.34 * coverage
                    + 0.10 * phase_diversity
                    + 0.06 * source_diversity
                    + 0.14 * evidence
                    + 0.08 * confidence
                    + 0.14 * bundle_success
                    + 0.06 * bundle_evidence
                    + 0.04 * bundle_confidence
                    + growth
                    + 0.16 * member_contribution
                    - 0.08 * cost
                    - 0.05 * normalized_latency
                    - 0.10 * redundancy
                )
                candidates.append((score, members, bundle_confidence, cost, latency, bundle_success, status, redundancy))
        if not candidates:
            raise LookupError("no safe capability bundle available for request")
        candidates.sort(key=lambda item: (-item[0], len(item[1]), tuple(o.name for o in item[1])))
        best_score, members, bundle_confidence, cost, latency, bundle_success, status, redundancy = candidates[0]
        selected = next((o for o in members if o.name == primary.selected), members[0])
        bundle_id = self.bundle_id(members)
        member_names = tuple(o.name for o in members)
        rationale = (
            primary.rationale
            + f"; bundle={bundle_id}; members={','.join(member_names)}; "
            + f"bundle_score={best_score:.3f}; bundle_status={status}; redundancy={redundancy:.3f}"
        )
        return CapabilityDecision(
            selected=selected.name,
            source=selected.source,
            score=primary.score,
            confidence=primary.confidence,
            rationale=rationale,
            alternatives=primary.alternatives,
            degraded=primary.degraded or status == "degraded",
            selected_set=member_names,
            bundle_id=bundle_id,
            bundle_score=best_score,
            bundle_confidence=max(bundle_confidence, bundle_success),
            bundle_status=status,
            bundle_cost=cost,
            bundle_latency_ms=latency * 1000.0,
            execution_groups=self._execution_groups(members),
        )

    @staticmethod
    def _exploration_need(prior: Mapping[str, float]) -> float:
        """Return a bounded exploration need from sample scarcity and confidence."""
        samples = max(0, int(float(prior.get("samples", 0))))
        confidence = max(0.0, min(1.0, float(prior.get("confidence", 0.25))))
        if samples >= 8 and confidence >= 0.75:
            return 0.0
        scarcity = 1.0 / (samples + 1.0) ** 0.5
        return max(0.0, min(1.0, (1.0 - confidence) * scarcity))

    @staticmethod
    def _allowed(option: CapabilityOption, network_allowed: bool, sandbox_available: bool, max_risk: str) -> bool:
        return (
            option.model_invocable
            and _RISK_ORDER.get(option.risk, 99) <= _RISK_ORDER[max_risk]
            and (network_allowed or not option.requires_network)
            and (sandbox_available or not option.requires_sandbox)
        )


@dataclass(frozen=True)
class QualityResult:
    status: str
    score: int
    findings: tuple[str, ...]


class OutputQualityGate:
    def evaluate(self, report: dict[str, Any] | None = None, *, acceptance_met: bool,
                 verification_passed: bool, evidence_count: int, diff_clean: bool,
                 scope_clean: bool, unresolved: int = 0) -> QualityResult:
        _ = report
        findings: list[str] = []; score = 100
        checks = ((acceptance_met, 35, "acceptance criteria not satisfied"),
                  (verification_passed, 35, "verification did not pass"),
                  (evidence_count > 0, 15, "no evidence supplied"),
                  (diff_clean, 10, "diff is not clean"),
                  (scope_clean, 5, "scope is not clean"),
                  (unresolved == 0, 5, "unresolved findings remain"))
        for ok, penalty, finding in checks:
            if not ok:
                findings.append(finding); score -= penalty
        return QualityResult("ready" if not findings else "blocked", max(0, score), tuple(findings))


__all__ = [
    "CAPABILITIES", "Capability", "CapabilityFabric", "ProviderAdapter", "ProviderAdapterRegistry",
    "MemoryRecord", "PersistentMemory", "DelegationReceipt", "DelegationPool", "Schedule",
    "AutomationScheduler", "Skill", "SkillRegistry", "CapabilityOption", "CapabilityDecision", "CapabilityExecutioner", "QualityResult", "OutputQualityGate",
    "redact", "sanitize_untrusted", "sanitize_capability_reference",
]
