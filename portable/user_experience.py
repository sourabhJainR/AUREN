"""Read-only end-user control surface for AUREN.

This module deliberately owns no new persistent state. It is a presentation and
health facade over existing installation metadata, scheduler state, provider
binaries, Agent Skills locations, repository intelligence, and work reports.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .repository_intelligence import RepositoryIntelligence


_PROVIDER_NAMES = ("claude", "codex", "gemini")
_SKILL_DESTINATIONS = {
    "agents": Path.home() / ".agents" / "skills" / "ai-coding-orchestrator",
    "claude": Path.home() / ".claude" / "skills" / "ai-coding-orchestrator",
    "gemini": Path.home() / ".gemini" / "skills" / "ai-coding-orchestrator",
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    message: str
    remedy: str | None = None


def _path(value: Path | str | None, default: Path) -> Path:
    return (Path(value).expanduser().resolve() if value is not None else default.expanduser().resolve())


def _default_auren_home() -> Path:
    return Path.home() / ".auren"


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def render_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def _installation(auren_home: Path) -> dict[str, object]:
    active_path = auren_home / "active.json"
    active = _safe_read_json(active_path)
    installed = active is not None and bool(active.get("version")) and bool(active.get("source_commit"))
    current = auren_home / "current"
    current_exists = current.exists() or current.is_symlink()
    consistent = False
    reason = "not installed"

    if installed and current_exists:
        try:
            install_meta = _safe_read_json(current / "install.json")
            consistent = (
                install_meta is not None
                and install_meta.get("version") == active.get("version")
                and install_meta.get("source_commit") == active.get("source_commit")
                and install_meta.get("bundle_sha256") == active.get("bundle_sha256")
            )
            reason = "healthy" if consistent else "active installation metadata is inconsistent"
        except OSError:
            reason = "active installation could not be inspected"
    elif installed:
        reason = "active metadata exists but the current installation is missing"

    versions = 0
    versions_root = auren_home / "versions"
    try:
        versions = sum(1 for p in versions_root.iterdir() if p.is_dir()) if versions_root.is_dir() else 0
    except OSError:
        versions = 0

    return {
        "installed": bool(installed and consistent),
        "declared": bool(installed),
        "version": active.get("version") if active else None,
        "source_commit": str(active.get("source_commit"))[:12] if active else None,
        "bundle_sha256": str(active.get("bundle_sha256"))[:16] if active else None,
        "versions": versions,
        "current_exists": current_exists,
        "reason": reason,
    }


def _providers() -> dict[str, object]:
    result: dict[str, object] = {}
    for name in _PROVIDER_NAMES:
        executable = shutil.which(name)
        result[name] = {
            "available": executable is not None,
            "path": executable,
        }
    return result


def _skills() -> dict[str, object]:
    return {
        name: {
            "installed": destination.is_dir(),
            "path": str(destination),
            "skill_file": (destination / "SKILL.md").is_file(),
        }
        for name, destination in _SKILL_DESTINATIONS.items()
    }


def _parse_schedule_label(task: object) -> tuple[str, str]:
    if isinstance(task, str):
        try:
            payload = json.loads(task)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            schedule = payload.get("schedule")
            kind = payload.get("kind")
            if schedule == "last_day_of_month":
                return "monthly maintenance", "calendar"
            if isinstance(kind, str) and kind:
                return kind.replace("_", " "), "kind"
    return "custom schedule", "custom"


def _scheduler(auren_home: Path) -> dict[str, object]:
    db_path = auren_home / "automation" / "automation.db"
    if not db_path.is_file():
        return {
            "available": False,
            "path": str(db_path),
            "schedules": [],
            "runs": 0,
        }

    schedules: list[dict[str, object]] = []
    total_runs = 0
    try:
        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=2) as db:
            rows = db.execute(
                "SELECT id,task,next_run,enabled,max_attempts,attempts FROM schedules ORDER BY id"
            ).fetchall()
            for schedule_id, task, next_run, enabled, max_attempts, attempts in rows:
                label, kind = _parse_schedule_label(task)
                recent = db.execute(
                    "SELECT status,finished_at FROM runs WHERE schedule_id=? ORDER BY finished_at DESC LIMIT 1",
                    (schedule_id,),
                ).fetchone()
                schedules.append({
                    "id": str(schedule_id),
                    "label": label,
                    "kind": kind,
                    "enabled": bool(enabled),
                    "next_run": next_run,
                    "attempts": int(attempts),
                    "max_attempts": int(max_attempts),
                    "last_status": recent[0] if recent else None,
                    "last_finished_at": recent[1] if recent else None,
                })
            total_runs = int(db.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
    except (OSError, sqlite3.Error):
        return {
            "available": False,
            "path": str(db_path),
            "schedules": [],
            "runs": 0,
            "error": "scheduler state could not be read",
        }

    return {
        "available": True,
        "path": str(db_path),
        "schedules": schedules,
        "runs": total_runs,
    }


def _history(auren_home: Path) -> dict[str, object]:
    history = auren_home / "history.jsonl"
    if not history.is_file():
        return {"events": 0, "activations": 0, "rollbacks": 0, "latest": None}
    activations = 0
    rollbacks = 0
    latest: dict[str, object] | None = None
    events = 0
    try:
        lines = history.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {"events": 0, "activations": 0, "rollbacks": 0, "latest": None}
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        event = record.get("event")
        if event not in {"activate", "rollback"}:
            continue
        events += 1
        if event == "activate":
            activations += 1
        else:
            rollbacks += 1
        latest = {
            "event": event,
            "version": record.get("version"),
            "source_commit": str(record.get("source_commit", ""))[:12],
            "installed_at": record.get("installed_at"),
        }
    return {"events": events, "activations": activations, "rollbacks": rollbacks, "latest": latest}


def _line_count(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def _observability(auren_home: Path) -> dict[str, object]:
    trace_path = auren_home / "observability" / "traces.jsonl"
    return {
        "enabled": trace_path.is_file(),
        "trace_file": str(trace_path),
        "trace_count": _line_count(trace_path),
        "bytes": trace_path.stat().st_size if trace_path.is_file() else 0,
    }


def _reports(project_root: Path) -> dict[str, object]:
    reports_root = project_root / ".ai-harness" / "reports"
    if not reports_root.is_dir():
        return {"available": False, "count": 0}
    try:
        count = sum(1 for path in reports_root.rglob("*.html") if path.is_file())
    except OSError:
        count = 0
    return {"available": True, "count": count}


def _repository(project_root: Path) -> dict[str, object]:
    git_dir = project_root / ".git"
    return {
        "root": str(project_root),
        "directory": project_root.is_dir(),
        "git": git_dir.exists(),
    }


def build_status(
    *,
    auren_home: Path | str | None = None,
    project_root: Path | str | None = None,
) -> dict[str, object]:
    home = _path(auren_home, _default_auren_home())
    project = _path(project_root, Path.cwd())
    return {
        "schema_version": 1,
        "installation": _installation(home),
        "providers": _providers(),
        "skills": _skills(),
        "repository": _repository(project),
        "scheduler": _scheduler(home),
        "activity": _history(home),
        "observability": _observability(home),
        "reports": _reports(project),
    }


def run_doctor(
    project_root: Path | str | None = None,
    *,
    auren_home: Path | str | None = None,
) -> tuple[list[CheckResult], int]:
    home = _path(auren_home, _default_auren_home())
    project = _path(project_root, Path.cwd())
    checks: list[CheckResult] = []

    installation = _installation(home)
    if installation["installed"]:
        checks.append(CheckResult("installation", "PASS", f"AUREN {installation['version']} is active"))
    elif installation["declared"]:
        checks.append(CheckResult(
            "installation",
            "FAIL",
            str(installation["reason"]),
            "Run verify/update or reinstall the pinned AUREN bundle.",
        ))
    else:
        checks.append(CheckResult(
            "installation",
            "WARN",
            "AUREN is not installed in this user profile",
            "Install the portable bundle before enabling implementation workflows.",
        ))

    if project.is_dir():
        if (project / ".git").exists():
            checks.append(CheckResult("project", "PASS", f"repository detected at {project}"))
        else:
            checks.append(CheckResult("project", "WARN", "project directory detected without a Git metadata directory"))
    else:
        checks.append(CheckResult("project", "FAIL", f"project directory does not exist: {project}"))

    providers = _providers()
    provider_count = sum(1 for value in providers.values() if isinstance(value, dict) and value.get("available"))
    checks.append(CheckResult(
        "providers",
        "PASS" if provider_count else "WARN",
        f"{provider_count} supported coding-agent host(s) detected",
        None if provider_count else "Install or configure a supported host such as Claude Code, Codex CLI, or Gemini CLI.",
    ))

    skills = _skills()
    skill_count = sum(1 for value in skills.values() if isinstance(value, dict) and value.get("installed") and value.get("skill_file"))
    checks.append(CheckResult(
        "skills",
        "PASS" if skill_count else "WARN",
        f"{skill_count} Agent Skills installation(s) detected",
        None if skill_count else "Install the AUREN skill for the coding-agent host you use.",
    ))

    scheduler = _scheduler(home)
    if scheduler.get("available"):
        checks.append(CheckResult("scheduler", "PASS", f"scheduler is readable with {len(scheduler['schedules'])} schedule(s)"))
    else:
        checks.append(CheckResult(
            "scheduler",
            "WARN",
            "no readable scheduler state is present",
            "This is normal before AUREN has executed a durable task; run a task first.",
        ))

    exit_code = 1 if any(check.status == "FAIL" for check in checks) else 0
    return checks, exit_code


def run_demo(project_root: Path | str, *, token_budget: int = 1200) -> dict[str, object]:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project root does not exist: {root}")
    if token_budget < 100:
        raise ValueError("token_budget must be at least 100")
    repo = RepositoryIntelligence.build(root)
    answer = repo.answer(
        "repository structure dependencies and tests",
        mode="search",
        token_budget=token_budget,
        max_files=8,
        context_lines=12,
        graph_depth=1,
    )
    return {
        "project_detected": True,
        "snapshot": answer.snapshot,
        "files": list(answer.files),
        "metrics": dict(answer.metrics),
        "token_estimate": answer.token_estimate,
        "unknowns": list(answer.unknowns),
        "next_stages": ["plan", "change", "verification", "review", "evidence"],
    }


def render_doctor(checks: list[CheckResult], exit_code: int, *, json_output: bool = False) -> str:
    if json_output:
        return render_json({"exit_code": exit_code, "checks": [asdict(check) for check in checks]})
    lines = ["AUREN doctor"]
    for check in checks:
        line = f"[{check.status}] {check.name}: {check.message}"
        if check.remedy:
            line += f" Remediation: {check.remedy}"
        lines.append(line)
    lines.append("Next: run `python auren_cli.py status` for the current control-plane snapshot.")
    return "\n".join(lines)


def render_status(snapshot: dict[str, object], *, json_output: bool = False) -> str:
    if json_output:
        return render_json(snapshot)

    installation = snapshot["installation"]
    providers = snapshot["providers"]
    skills = snapshot["skills"]
    repository = snapshot["repository"]
    scheduler = snapshot["scheduler"]
    activity = snapshot["activity"]
    observability = snapshot["observability"]
    reports = snapshot["reports"]

    provider_names = [name for name, value in providers.items() if isinstance(value, dict) and value.get("available")]
    skill_names = [name for name, value in skills.items() if isinstance(value, dict) and value.get("installed")]
    schedule_count = len(scheduler.get("schedules", [])) if isinstance(scheduler, dict) else 0
    latest = activity.get("latest") if isinstance(activity, dict) else None
    latest_text = "none"
    if isinstance(latest, dict):
        latest_text = f"{latest.get('event')} {latest.get('version') or ''}".strip()

    lines = [
        "AUREN status",
        f"Installation: {'ready' if installation.get('installed') else 'not ready'}" + (f" (v{installation.get('version')})" if installation.get('version') else ""),
        f"Project: {'Git repository' if repository.get('git') else 'directory'} at {repository.get('root')}",
        f"Hosts: {', '.join(provider_names) if provider_names else 'none detected'}",
        f"Skills: {', '.join(skill_names) if skill_names else 'none installed'}",
        f"Schedules: {schedule_count}",
        f"Runs recorded: {scheduler.get('runs', 0) if isinstance(scheduler, dict) else 0}",
        f"Latest installation event: {latest_text}",
        f"Observability traces: {observability.get('trace_count', 0) if isinstance(observability, dict) else 0}",
        f"Engineering reports: {reports.get('count', 0) if isinstance(reports, dict) else 0}",
        "",
        "Next: `python auren_cli.py doctor` or `python auren_cli.py demo .`",
    ]
    return "\n".join(lines)


__all__ = [
    "CheckResult",
    "build_status",
    "render_doctor",
    "render_json",
    "render_status",
    "run_demo",
    "run_doctor",
]
