"""Live AUREN/Muxin session view and bounded local-model discovery.

This adapter is read-only with respect to task execution: it projects canonical
AUREN checkpoints and dashboard evidence without creating a parallel run store.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .engineering_dashboard import EngineeringDashboard
from .session_state import SessionCheckpoint, SessionStore

_STAGE_ORDER = (
    "queued", "planning", "running", "testing", "reviewing", "verified", "completed"
)
_STAGE_PROGRESS = {name: round(index * 100 / (len(_STAGE_ORDER) - 1)) for index, name in enumerate(_STAGE_ORDER)}
_TERMINAL_FAILURES = {"failed", "blocked", "cancelled", "canceled"}
_MAX_SESSIONS = 100
_MAX_RESPONSE_BYTES = 1024 * 1024


def stage_progress(stage: str) -> int:
    """Return progress only for recognized stages; unknown stages stay unverified."""
    normalized = (stage or "").strip().lower()
    return _STAGE_PROGRESS.get(normalized, 0)


class MuxinSessionAdapter:
    """Project canonical AUREN session checkpoints into dashboard-safe records."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        session_store: SessionStore | None = None,
        dashboard: EngineeringDashboard | None = None,
        learning_reader: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.session_store = session_store or SessionStore()
        self.dashboard = dashboard or EngineeringDashboard(self.project_root)
        self.learning_reader = learning_reader

    def sessions(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            paths = sorted(self.session_store.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return rows
        for path in paths[:_MAX_SESSIONS]:
            checkpoint = self.session_store.load(path.stem)
            if checkpoint is None:
                continue
            if checkpoint.project_root:
                try:
                    if Path(checkpoint.project_root).expanduser().resolve() != self.project_root:
                        continue
                except OSError:
                    continue
            rows.append(self._session_record(checkpoint))
        return rows

    @staticmethod
    def _session_record(checkpoint: SessionCheckpoint) -> dict[str, Any]:
        stage = (checkpoint.stage or "unknown").strip().lower()
        progress = stage_progress(stage)
        status = "blocked" if stage in _TERMINAL_FAILURES or checkpoint.last_error else (
            "complete" if stage in {"completed", "verified"} else
            "waiting" if stage == "queued" else "running"
        )
        return {
            "session_id": checkpoint.session_id,
            "task_id": checkpoint.task_id,
            "stage": stage,
            "status": status,
            "progress": progress,
            "attempt": checkpoint.attempt,
            "active_provider": checkpoint.active_provider,
            "updated_at": checkpoint.updated_at,
            "completed_batches": list(checkpoint.completed_batches),
            "remaining_batches": list(checkpoint.remaining_batches),
            "last_error": checkpoint.last_error,
            "state_digest": checkpoint.state_digest,
            "evidence": [],
        }

    def snapshot(self) -> dict[str, Any]:
        dashboard = self.dashboard.snapshot().as_dict()
        sessions = self.sessions()
        learnings: list[dict[str, Any]] = []
        if self.learning_reader is not None:
            try:
                learnings = self.learning_reader()
            except Exception:
                # A failed optional learning projection must not break the console.
                learnings = []
        return {
            "schema_version": 1,
            "generated_at": dashboard.get("generated_at"),
            "project": dashboard.get("project"),
            "sessions": sessions,
            "learnings": learnings[:100],
            "dashboard": dashboard,
            "source": "AUREN durable checkpoints and engineering dashboard",
        }


class LocalModelDiscovery:
    """Discover an explicitly configured local Ollama-compatible endpoint.

    Probes are bounded and localhost-only by default. No prompt or user data is sent.
    """

    def __init__(self, base_url: str | None = None, timeout_seconds: float = 0.75) -> None:
        configured = base_url or os.environ.get("AUREN_LOCAL_MODEL_URL", "http://127.0.0.1:11434")
        if not configured.startswith(("http://127.0.0.1:", "http://localhost:", "http://[::1]:")):
            raise ValueError("local model endpoint must use loopback HTTP")
        self.base_url = configured.rstrip("/")
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 2.0))

    def discover(self) -> dict[str, Any]:
        started = time.monotonic()
        request = urllib.request.Request(self.base_url + "/api/tags", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(payload) > _MAX_RESPONSE_BYTES:
                    raise ValueError("local model response exceeds size limit")
            value = json.loads(payload.decode("utf-8"))
            models = value.get("models", []) if isinstance(value, dict) else []
            safe_models = [
                {"name": str(item.get("name", ""))[:200], "size_bytes": item.get("size")}
                for item in models[:100] if isinstance(item, dict) and item.get("name")
            ]
            return {
                "available": True, "provider": "ollama-compatible", "models": safe_models,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "resource_usage": {"process_metrics": "not exposed by endpoint"},
                "fallback": "not_attempted",
            }
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return {
                "available": False, "provider": "ollama-compatible", "models": [],
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "resource_usage": {"process_metrics": "not available"},
                "fallback": "not_attempted",
                "reason": type(exc).__name__,
            }


__all__ = ["LocalModelDiscovery", "MuxinSessionAdapter", "stage_progress"]
