"""Bounded cognitive episode coordination for AUREN execution.

The cognitive loop observes an orchestration episode without taking execution
authority away from the existing StateGraph/Orchestrator path. It records only
small, normalized observations and can persist them through canonical memory.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from .cognitive_runtime import CognitiveRuntime


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CognitiveEpisodeReceipt:
    episode_id: str
    project_key: str
    task_id: str
    intent: str
    status: str
    phases: tuple[str, ...]
    observations: tuple[dict[str, Any], ...]
    started_at: str
    finished_at: str
    digest: str
    error: str | None = None
    persistence_errors: tuple[str, ...] = ()


@dataclass
class CognitiveEpisode:
    episode_id: str
    project_key: str
    task_id: str
    intent: str
    started_at: str
    _phases: list[str] = field(default_factory=list)
    _observations: list[dict[str, Any]] = field(default_factory=list)
    _persistence_errors: list[str] = field(default_factory=list)

    @classmethod
    def start(cls, project_key: str, task_id: str, intent: str) -> "CognitiveEpisode":
        return cls(uuid4().hex, project_key, task_id, intent, _utc())

    def record_phase(self, phase: str) -> None:
        if not phase or phase in self._phases:
            return
        self._phases.append(phase)

    def observe(self, observation: Mapping[str, Any]) -> None:
        normalized = {str(key): value for key, value in observation.items()}
        normalized.setdefault("sequence", len(self._observations))
        self._observations.append(normalized)

    def finish(self, status: str, error: str | None = None) -> CognitiveEpisodeReceipt:
        payload = {
            "episode_id": self.episode_id,
            "project_key": self.project_key,
            "task_id": self.task_id,
            "intent": self.intent,
            "status": status,
            "phases": self._phases,
            "observations": self._observations,
            "persistence_errors": self._persistence_errors,
        }
        return CognitiveEpisodeReceipt(
            episode_id=self.episode_id,
            project_key=self.project_key,
            task_id=self.task_id,
            intent=self.intent,
            status=status,
            phases=tuple(self._phases),
            observations=tuple(dict(item) for item in self._observations),
            started_at=self.started_at,
            finished_at=_utc(),
            digest=_digest(payload),
            error=error,
            persistence_errors=tuple(self._persistence_errors),
        )


class CognitiveLoop:
    """Coordinate a bounded cognitive episode around an existing execution."""

    def __init__(self, cognitive: CognitiveRuntime | None = None) -> None:
        self.cognitive = cognitive

    def begin(self, project_key: str, task_id: str, intent: str) -> CognitiveEpisode:
        episode = CognitiveEpisode.start(project_key, task_id, intent)
        episode.record_phase("observe")
        self._persist(episode, {"event": "episode_started", "intent": intent})
        return episode

    def observe(self, episode: CognitiveEpisode, observation: Mapping[str, Any]) -> None:
        episode.observe(observation)
        self._persist(episode, observation)

    def complete(self, episode: CognitiveEpisode, status: str, error: str | None = None) -> CognitiveEpisodeReceipt:
        episode.record_phase("evaluate")
        if status in {"accepted", "completed", "success"}:
            episode.record_phase("learn")
        episode.record_phase("complete")
        receipt = episode.finish(status, error)
        self._persist(episode, {"event": "episode_completed", "status": status, "digest": receipt.digest, "error": error})
        return receipt

    def _persist(self, episode: CognitiveEpisode, observation: Mapping[str, Any]) -> None:
        if self.cognitive is None:
            return
        compact = {
            "episode_id": episode.episode_id,
            "task_id": episode.task_id,
            "event": str(observation.get("event", "observation")),
            "status": observation.get("status"),
            "phase": episode._phases[-1] if episode._phases else None,
            "digest": _digest({str(k): v for k, v in observation.items()}),
        }
        text = json.dumps(compact, sort_keys=True, separators=(",", ":"), default=str)
        try:
            self.cognitive.memory.remember(
                episode.project_key,
                "cognitive_episode",
                text,
                confidence=1.0,
                verified=True,
                approved=True,
            )
        except Exception as exc:
            episode._persistence_errors.append(f"{type(exc).__name__}: {exc}")


__all__ = ["CognitiveEpisode", "CognitiveEpisodeReceipt", "CognitiveLoop"]
