"""AER engineering observability snapshot and local dashboard data service."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable
from .repository_intelligence import RepositoryIntelligence

def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def _json(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return str(value)

@dataclass(frozen=True)
class DashboardSnapshot:
    generated_at: str
    project: str
    repository: dict[str, Any]
    executions: tuple[dict[str, Any], ...]
    tasks: dict[str, Any]
    learnings: dict[str, Any]
    findings: dict[str, Any]
    usage: dict[str, Any]
    quality: dict[str, Any]
    benchmarks: dict[str, Any]
    research: dict[str, Any]
    tests: dict[str, Any]
    graph: dict[str, Any]
    def as_dict(self) -> dict[str, Any]:
        return _json(asdict(self))

class EngineeringDashboard:
    """Observation-only dashboard over existing AER state."""
    def __init__(self, project_root: str | Path, *, project: str | None = None) -> None:
        self.root = Path(project_root).resolve()
        self.project = project or self.root.name
        self.state_dir = self.root / ".aer" / "dashboard"
        self.event_path = self.state_dir / "events.jsonl"

    def record_event(self, *, run_id: str, event: str, status: str = "running",
                     task_id: str = "", detail: str = "", started_at: str = "",
                     finished_at: str = "", duration_ms: int | None = None,
                     metadata: dict[str, Any] | None = None) -> None:
        if not run_id.strip() or not event.strip():
            raise ValueError("run_id and event are required")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": _utc(), "run_id": run_id.strip(), "event": event.strip(),
            "status": status.strip() or "running", "task_id": task_id.strip(),
            "detail": detail, "started_at": started_at, "finished_at": finished_at,
            "duration_ms": duration_ms, "metadata": _json(metadata or {}),
        }
        with self.event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def snapshot(self) -> DashboardSnapshot:
        repo = RepositoryIntelligence.build(self.root)
        dbs = self._sqlite_files()
        tables = self._table_counts(dbs)
        events = self._events()
        tests = self._tests_snapshot()
        return DashboardSnapshot(
            generated_at=_utc(), project=self.project,
            repository={"root": str(self.root), "snapshot": repo.digest(),
                        "files": len(repo.files),
                        "symbols": sum(len(f.symbols) for f in repo.files.values()),
                        "edges": len(repo.edges)},
            executions=self._executions(events),
            tasks=self._tasks(tables, events),
            learnings=self._learnings(tables),
            findings=self._findings(tables, events),
            usage=self._usage(tables, events, dbs),
            quality=self._quality(repo, tests, tables),
            benchmarks=self._benchmarks(tables),
            research=self._research(tables),
            tests=tests,
            graph=self._graph(repo),
        )

    def _sqlite_files(self) -> tuple[Path, ...]:
        candidates = []
        for base in (self.root / ".aer", self.root / "state", self.root / ".ai-harness", self.root):
            if not base.exists():
                continue
            try:
                candidates.extend(p for p in base.rglob("*")
                                 if p.is_file() and p.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
                                 and ".git" not in p.parts)
            except OSError:
                continue
        seen, result = set(), []
        for path in sorted(candidates):
            key = str(path)
            if key not in seen:
                seen.add(key); result.append(path)
        return tuple(result)

    def _table_counts(self, paths: Iterable[Path]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for path in paths:
            try:
                uri = f"file:{path.as_posix()}?mode=ro"
                with sqlite3.connect(uri, uri=True, timeout=0.5) as db:
                    names = [r[0] for r in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                    for name in names:
                        try:
                            count = int(db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
                        except sqlite3.Error:
                            continue
                        counts[name] = counts.get(name, 0) + count
            except (OSError, sqlite3.Error):
                continue
        return counts

    def _events(self) -> tuple[dict[str, Any], ...]:
        if not self.event_path.exists():
            return ()
        rows = []
        try:
            lines = self.event_path.read_text(encoding="utf-8").splitlines()[-5000:]
        except OSError:
            return ()
        for line in lines:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except json.JSONDecodeError:
                continue
        return tuple(rows)

    @staticmethod
    def _sum(tables: dict[str, int], needles: Iterable[str]) -> int:
        return sum(v for k, v in tables.items() if any(n in k.lower() for n in needles))

    def _executions(self, events: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
        latest = {}
        for row in events:
            if row.get("run_id"):
                latest[str(row["run_id"])] = row
        active = [r for r in latest.values() if r.get("status") in {"running", "executing", "started"}]
        return tuple(sorted(active, key=lambda r: str(r.get("timestamp", "")), reverse=True)[:50])

    def _tasks(self, tables, events):
        ids = {str(e.get("task_id")) for e in events if e.get("task_id")}
        completed = self._sum(tables, ("experience_history", "regression_cases", "episode"))
        return {"observed": max(completed, len(ids)), "event_runs": len({e.get("run_id") for e in events if e.get("run_id")}),
                "completed_like_records": completed}

    def _learnings(self, tables):
        return {"deferred_jobs": self._sum(tables, ("deferred_learning",)),
                "skill_epochs": self._sum(tables, ("skill_optimization_epochs",)),
                "regression_cases": self._sum(tables, ("regression_cases",)),
                "maintenance_receipts": self._sum(tables, ("maintenance_receipts",))}

    def _findings(self, tables, events):
        return {"observed": self._sum(tables, ("finding", "review")),
                "failures": self._sum(tables, ("failure",)),
                "dont_rules": self._sum(tables, ("failure_dont", "dont")),
                "event_failures": sum(1 for e in events if e.get("status") in {"failed", "error"})}

    def _usage(self, tables, events, dbs):
        durations = [int(e["duration_ms"]) for e in events
                     if isinstance(e.get("duration_ms"), (int, float)) and e["duration_ms"] >= 0]
        return {"runs": len({e.get("run_id") for e in events if e.get("run_id")}),
                "events": len(events), "sqlite_stores": len(dbs),
                "event_duration_ms": sum(durations),
                "average_task_duration_ms": round(sum(durations) / len(durations)) if durations else None}

    def _quality(self, repo, tests, tables):
        source_files = len(repo.files)
        test_files = tests["files"]
        return {"source_files": source_files, "test_files": test_files,
                "test_file_ratio": round(test_files / source_files, 3) if source_files else 0.0,
                "graph_edges": len(repo.edges),
                "verified_records": self._sum(tables, ("verification", "evidence")),
                "note": "Repository indicators are descriptive. Recorded verification data is shown when present; no synthetic quality score is invented."}

    def _benchmarks(self, tables):
        return {"regression_cases": self._sum(tables, ("regression_cases",)),
                "validations": self._sum(tables, ("regression_validations",)),
                "benchmark_records": self._sum(tables, ("benchmark", "evaluation")),
                "skill_optimization_epochs": self._sum(tables, ("skill_optimization_epochs",))}

    def _research(self, tables):
        return {"research_records": self._sum(tables, ("research", "hypothesis", "information")),
                "capability_experiments": self._sum(tables, ("capability", "experiment"))}

    def _tests_snapshot(self):
        files, cases = [], 0
        test_root = self.root / "tests"
        paths = sorted(test_root.rglob("test_*.py")) if test_root.exists() else ()
        for path in paths:
            files.append(str(path.relative_to(self.root)).replace("\\", "/"))
            try:
                text = path.read_text(encoding="utf-8")
                cases += len(re.findall(r"^\s*(?:async\s+)?def\s+test_", text, re.M))
            except OSError:
                continue
        return {"files": len(files), "cases": cases, "paths": files[-100:]}

    def _graph(self, repo):
        by_kind = {}
        for edge in repo.edges:
            by_kind[edge.kind] = by_kind.get(edge.kind, 0) + 1
        connected = []
        for path in repo.files:
            degree = sum(1 for e in repo.edges if e.source_path == path or e.target_path == path)
            connected.append((path, degree))
        return {"nodes": len(repo.files), "edges": len(repo.edges), "edge_kinds": by_kind,
                "top_files": sorted(connected, key=lambda x: (-x[1], x[0]))[:12]}

__all__ = ["DashboardSnapshot", "EngineeringDashboard"]
