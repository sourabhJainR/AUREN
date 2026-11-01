"""Stable, provider-neutral Python SDK for the AUREN durable runtime.

The SDK is a thin facade over canonical runtime services. It does not create a
second scheduler, memory store, progress journal, or policy authority.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from .engineering_dashboard import EngineeringDashboard
from .execution_controller import ExecutionController
from .persistent_memory import PersistentMemory

SDK_API_VERSION = "v1"
EXECUTION_CLASSES = frozenset({"read_only", "context_provider", "ci_gated", "consequential"})


class AurenClient:
    """Own the canonical runtime, progress journal, dashboard and memory facade."""

    api_version = SDK_API_VERSION

    def __init__(
        self,
        project_root: str | Path,
        *,
        memory_path: str | Path | None = None,
        dashboard_enabled: bool = True,
        agent_assignment_verifier: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        if not self.project_root.exists() or not self.project_root.is_dir():
            raise ValueError("project_root must be an existing directory")
        self.controller = ExecutionController(
            self.project_root, agent_assignment_verifier=agent_assignment_verifier
        )
        self.trusted_plan_dispatch_root = None
        resolved_memory_path = (
            Path(memory_path).expanduser().resolve()
            if memory_path is not None
            else self.project_root / ".auren" / "memory.sqlite3"
        )
        self.memory = PersistentMemory(resolved_memory_path)
        self.dashboard = EngineeringDashboard(self.project_root) if dashboard_enabled else None

    def configure_trusted_plan_dispatch(
        self,
        *,
        store: Any,
        recovery_bridge: Any,
        approval_store: Any,
        budget_resolver: Callable[..., Mapping[str, Any]],
        approval_resolver: Callable[..., Mapping[str, Any] | None],
    ) -> Any:
        """Install host-owned budget and approval resolvers before plan dispatch."""
        if self.trusted_plan_dispatch_root is not None:
            raise RuntimeError("trusted plan dispatch is already configured")
        from .trusted_plan_dispatch import TrustedPlanDispatchRoot
        self.trusted_plan_dispatch_root = TrustedPlanDispatchRoot(
            controller=self.controller,
            store=store,
            recovery_bridge=recovery_bridge,
            approval_store=approval_store,
            budget_resolver=budget_resolver,
            approval_resolver=approval_resolver,
        )
        return self.trusted_plan_dispatch_root

    def dispatch_engineering_plan_task(self, **request: Any) -> Any:
        """Use the trusted composition root; fail closed when host policy is absent."""
        if self.trusted_plan_dispatch_root is None:
            raise RuntimeError("trusted plan dispatch root is not configured; refusing unbound dispatch")
        return self.trusted_plan_dispatch_root.dispatch(**request)

    def register_handler(
        self,
        name: str,
        handler: Callable[..., Any] | str,
        *,
        execution_class: str,
        ci_gate_policy: Mapping[str, Any] | None = None,
        promotion_gate_policy: Mapping[str, Any] | None = None,
        agent_action_class: str | None = None,
    ) -> str:
        """Register an importable durable handler with an explicit risk class."""
        if execution_class not in EXECUTION_CLASSES:
            raise ValueError(
                "execution_class must be read_only, context_provider, ci_gated, or consequential"
            )
        return self.controller.register_handler(
            name,
            handler,
            execution_class=execution_class,
            ci_gate_policy=ci_gate_policy,
            promotion_gate_policy=promotion_gate_policy,
            agent_action_class=agent_action_class,
        )

    def submit(
        self, handler: str, payload: Mapping[str, Any], *, goal: str | None = None
    ) -> Any:
        """Persist a task. Submission does not imply that execution has started."""
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        return self.controller.submit(handler, dict(payload), goal=goal)

    def run_once(self, task_id: str) -> Any:
        """Ask the canonical controller to process one task attempt."""
        return self.controller.run_once(task_id)

    def task(self, task_id: str) -> Any:
        """Return the durable task record, or None when the ID is unknown."""
        return self.controller.task(task_id)

    def resume_pending(self, *, limit: int = 100) -> Any:
        """Resume eligible work through the controller's recovery rules."""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        return self.controller.resume_pending(limit=limit)

    def progress(self, task_id: str, *, stale_after_seconds: int = 60) -> Any:
        """Read progress from the controller-owned append-only journal."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required")
        if type(stale_after_seconds) is not int or stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be a positive integer")
        return self.controller.progress_journal.snapshot(
            task_id, stale_after_seconds=stale_after_seconds
        )

    def dashboard_snapshot(self) -> dict[str, Any]:
        """Return an observational projection; never use it as execution authority."""
        if self.dashboard is None:
            raise RuntimeError("dashboard projection is disabled")
        return self.dashboard.snapshot().as_dict()

    def remember(
        self, project: str, category: str, text: str, *,
        approved: bool = False, **metadata: Any
    ) -> Any:
        """Write to canonical memory only when the caller grants write approval."""
        if type(approved) is not bool:
            raise ValueError("approved must be a boolean")
        return self.memory.remember(
            project, category, text, approved=approved, **metadata
        )

    def search_memory(self, project: str, query: str, **filters: Any) -> Any:
        """Search canonical memory. Results retain their stored provenance."""
        return self.memory.search(project, query, **filters)

    def health_findings(self) -> tuple[str, ...]:
        """Return runtime health findings without converting them to success."""
        return tuple(self.controller.health_findings())

    def close(self) -> None:
        """Release SDK-owned resources. Durable task state remains on disk."""
        self.memory.close()


__all__ = ["AurenClient", "EXECUTION_CLASSES", "SDK_API_VERSION"]
