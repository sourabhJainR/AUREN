"""Durable continuous engineering runtime.

Persists episode state and decisions so engineering can resume after process
restarts. It composes the existing evidence, remediation, prediction and
evolution primitives while keeping repository mutation behind an injected
executor.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .autonomous_engineering_loop import AutonomousEngineeringLoop, EngineeringLoopReceipt
from .autonomous_evolution_controller import AutonomousEvolutionController, EvolutionTrigger
from .engineering_evolution import EngineeringEvolutionControlPlane
from .persistent_memory import PersistentMemory
from .multi_hat_self_review import SelfReviewReport


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:20]


@dataclass(frozen=True)
class EngineeringEpisodeState:
    episode_id: str
    project: str
    task_family: str
    capability: str
    state: str
    iteration: int
    plan_digest: str
    evidence_ids: tuple[str, ...]
    remediation_ids: tuple[str, ...]
    evolution_triggered: bool
    terminal_action: str
    updated_at: str
    last_error: str = ""


@dataclass(frozen=True)
class ContinuousEngineeringReceipt:
    state: EngineeringEpisodeState
    loop: EngineeringLoopReceipt | None
    evolution: EvolutionTrigger | None
    resumed: bool


class ContinuousEngineeringRuntime:
    """Run bounded episodes with durable state and evidence-driven evolution."""

    STATES = frozenset({
        "planned", "running", "verifying", "learning",
        "completed", "escalated", "failed",
    })

    def __init__(
        self,
        memory: PersistentMemory,
        project: str,
        *,
        max_iterations: int = 5,
        evolution_threshold: int = 3,
    ) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be PersistentMemory")
        if not project.strip():
            raise ValueError("project is required")
        self.memory = memory
        self.project = project.strip()
        self.control_plane = EngineeringEvolutionControlPlane(memory, self.project)
        self.loop = AutonomousEngineeringLoop(
            self.control_plane, max_iterations=max_iterations
        )
        self.evolution = AutonomousEvolutionController(
            memory, self.project, threshold=evolution_threshold
        )
        with self.memory._lock, self.memory._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS engineering_episodes(
                  project TEXT NOT NULL, episode_id TEXT NOT NULL,
                  task_family TEXT NOT NULL, capability TEXT NOT NULL,
                  state TEXT NOT NULL, iteration INTEGER NOT NULL,
                  plan_digest TEXT NOT NULL, evidence_json TEXT NOT NULL,
                  remediation_json TEXT NOT NULL, evolution_triggered INTEGER NOT NULL,
                  terminal_action TEXT NOT NULL, last_error TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(project, episode_id)
                )"""
            )

    def _load(self, episode_id: str) -> EngineeringEpisodeState | None:
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute(
                """SELECT episode_id,project,task_family,capability,state,iteration,
                   plan_digest,evidence_json,remediation_json,evolution_triggered,
                   terminal_action,updated_at,last_error
                   FROM engineering_episodes WHERE project=? AND episode_id=?""",
                (self.project, episode_id),
            ).fetchone()
        if not row:
            return None
        return EngineeringEpisodeState(
            row[0], row[1], row[2], row[3], row[4], int(row[5]), row[6],
            tuple(json.loads(row[7])), tuple(json.loads(row[8])), bool(row[9]),
            row[10], row[11], row[12],
        )

    def _save(
        self,
        *,
        episode_id: str,
        task_family: str,
        capability: str,
        state: str,
        iteration: int,
        plan_digest: str = "",
        evidence_ids: Iterable[str] = (),
        remediation_ids: Iterable[str] = (),
        evolution_triggered: bool = False,
        terminal_action: str = "",
        last_error: str = "",
    ) -> EngineeringEpisodeState:
        if state not in self.STATES:
            raise ValueError(f"invalid episode state: {state}")
        evidence = tuple(dict.fromkeys(str(x) for x in evidence_ids if str(x)))
        remediation = tuple(dict.fromkeys(str(x) for x in remediation_ids if str(x)))
        with self.memory._lock, self.memory._connect() as db:
            db.execute(
                """INSERT INTO engineering_episodes
                   (project,episode_id,task_family,capability,state,iteration,plan_digest,
                    evidence_json,remediation_json,evolution_triggered,terminal_action,
                    last_error,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project,episode_id) DO UPDATE SET
                    task_family=excluded.task_family, capability=excluded.capability,
                    state=excluded.state, iteration=excluded.iteration,
                    plan_digest=excluded.plan_digest, evidence_json=excluded.evidence_json,
                    remediation_json=excluded.remediation_json,
                    evolution_triggered=excluded.evolution_triggered,
                    terminal_action=excluded.terminal_action,
                    last_error=excluded.last_error, updated_at=excluded.updated_at""",
                (
                    self.project, episode_id, task_family, capability, state, iteration,
                    plan_digest, json.dumps(evidence), json.dumps(remediation),
                    int(evolution_triggered), terminal_action, last_error, _utc(),
                ),
            )
        return self._load(episode_id)  # type: ignore[return-value]

    def run(
        self,
        *,
        episode_id: str,
        task_family: str,
        capability: str,
        execute: Callable[[Any], Any],
        verify: Callable[[Any], tuple[bool, Iterable[str]]],
        learn: Callable[[Any, tuple[str, ...]], None] | None = None,
        promote: Callable[[Any, tuple[str, ...]], bool] | None = None,
        failure_threshold: float = 0.5,
        on_evolution_trigger: Callable[[EvolutionTrigger], None] | None = None,
        self_review: Callable[[Any, tuple[str, ...]], SelfReviewReport] | None = None,
    ) -> ContinuousEngineeringReceipt:
        for value, name in (
            (episode_id, "episode_id"), (task_family, "task_family"),
            (capability, "capability"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")

        existing = self._load(episode_id)
        if existing and existing.state in {"completed", "escalated", "failed"}:
            return ContinuousEngineeringReceipt(existing, None, None, True)
        # A crash after the execution checkpoint leaves the episode in
        # "verifying". Re-executing unknown work could risk duplicate mutation,
        # so recovery is explicitly fail-closed.
        if existing and existing.state == "verifying":
            state = self._save(
                episode_id=episode_id, task_family=task_family, capability=capability,
                state="escalated", iteration=existing.iteration,
                plan_digest=existing.plan_digest, evidence_ids=existing.evidence_ids,
                remediation_ids=existing.remediation_ids,
                terminal_action="manual_verification_required",
                last_error="execution checkpoint exists without a durable execution receipt",
            )
            return ContinuousEngineeringReceipt(state, None, None, True)

        resumed = existing is not None
        if existing and (
            existing.task_family != task_family or existing.capability != capability
        ):
            raise ValueError("episode identity does not match persisted task")
        start_iteration = existing.iteration if existing else 0
        self._save(
            episode_id=episode_id, task_family=task_family, capability=capability,
            state="running", iteration=start_iteration,
            evidence_ids=existing.evidence_ids if existing else (),
            remediation_ids=existing.remediation_ids if existing else (),
        )

        try:
            receipt = self.loop.run(
                episode_id=episode_id,
                task_family=task_family,
                capability=capability,
                execute=self._execute_checkpointed(
                    episode_id, task_family, capability, execute
                ),
                verify=verify,
                learn=learn,
                promote=promote,
                failure_threshold=failure_threshold,
                self_review=self_review,
            )
        except Exception as exc:
            state = self._load(episode_id) or self._save(
                episode_id=episode_id, task_family=task_family, capability=capability,
                state="failed", iteration=start_iteration,
            )
            state = self._save(
                episode_id=episode_id, task_family=task_family, capability=capability,
                state="failed", iteration=state.iteration,
                evidence_ids=state.evidence_ids, remediation_ids=state.remediation_ids,
                last_error=f"{type(exc).__name__}: {exc}",
                terminal_action="failed",
            )
            raise

        evolution_trigger = None
        if receipt.terminal_action in {"escalate", "promotion_blocked"}:
            problem = f"{task_family}:{capability}:{receipt.terminal_action}"
            ids = receipt.evidence_ids or receipt.remediation_ids or (episode_id,)
            for evidence_id in ids:
                evolution_trigger = self.evolution.observe_failure(
                    problem, evidence_id=str(evidence_id), unresolved=True
                )
            if evolution_trigger and evolution_trigger.triggered and on_evolution_trigger:
                on_evolution_trigger(evolution_trigger)

        state = self._save(
            episode_id=episode_id, task_family=task_family, capability=capability,
            state=("completed" if receipt.accepted else "escalated"),
            iteration=max(start_iteration, start_iteration + receipt.iterations),
            plan_digest=_digest([
                d.decomposition.source_findings if d.decomposition else ()
                for d in receipt.decisions
            ]),
            evidence_ids=receipt.evidence_ids,
            remediation_ids=receipt.remediation_ids,
            evolution_triggered=bool(evolution_trigger and evolution_trigger.triggered),
            terminal_action=receipt.terminal_action,
        )
        return ContinuousEngineeringReceipt(state, receipt, evolution_trigger, resumed)

    def _execute_checkpointed(
        self,
        episode_id: str,
        task_family: str,
        capability: str,
        execute: Callable[[Any], Any],
    ) -> Callable[[Any], Any]:
        def wrapped(plan: Any) -> Any:
            digest = _digest(getattr(plan, "tasks", plan))
            current = self._load(episode_id)
            self._save(
                episode_id=episode_id, task_family=task_family, capability=capability,
                state="verifying", iteration=(current.iteration if current else 0) + 1,
                plan_digest=digest,
                evidence_ids=current.evidence_ids if current else (),
                remediation_ids=current.remediation_ids if current else (),
            )
            return execute(plan)
        return wrapped

    def status(self, episode_id: str) -> EngineeringEpisodeState | None:
        return self._load(episode_id)


__all__ = ["EngineeringEpisodeState", "ContinuousEngineeringReceipt", "ContinuousEngineeringRuntime"]
