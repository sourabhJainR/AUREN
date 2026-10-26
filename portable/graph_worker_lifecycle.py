"""StateGraph node adapter for isolated mutation, independent verification, and durable progress.

This module is a worker lifecycle adapter, not a scheduler. TaskPlan and StateGraph
remain the only task planner and scheduler; the coordinator serializes sibling adoption.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
import uuid
from typing import Any, Callable, Mapping, Sequence

from portable.graph_workspace_adoption import GraphWorkspaceCoordinator, WorkspaceProposal
from portable.engineering_recovery import GraphRunRecoveryBridge
from portable.independent_graph_verification import IndependentGraphVerifier
from portable.progress_journal import ProgressJournal
from portable.runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard
from portable.task_planner import TaskPlan


class GraphWorkerLifecycleError(RuntimeError):
    """A graph worker could not produce independently verified adoptable work."""


@dataclass(frozen=True)
class PreparedGraphWorker:
    task_id: str
    worker_id: str
    task_digest: str
    intent_digest: str
    proposal: WorkspaceProposal
    receipt: Mapping[str, Any]
    receipt_path: str
    workspace_id: str


class GraphWorkerLifecycle:
    """Prepare mutating worker proposals and adopt a verified sibling set once."""

    def __init__(
        self,
        *,
        coordinator: GraphWorkspaceCoordinator,
        verifier: IndependentGraphVerifier,
        journal: ProgressJournal,
        task_plan: TaskPlan,
        intent_digest: str,
        runtime_guard: RuntimeExecutionGuard,
        recovery_bridge: GraphRunRecoveryBridge,
    ) -> None:
        if not isinstance(coordinator, GraphWorkspaceCoordinator):
            raise TypeError("coordinator must be the canonical GraphWorkspaceCoordinator")
        if not isinstance(verifier, IndependentGraphVerifier):
            raise TypeError("verifier must be an IndependentGraphVerifier")
        if not isinstance(journal, ProgressJournal):
            raise TypeError("journal must be the canonical ProgressJournal")
        if not isinstance(task_plan, TaskPlan):
            raise TypeError("task_plan must be the canonical TaskPlan")
        if not isinstance(runtime_guard, RuntimeExecutionGuard):
            raise TypeError("runtime_guard must be the caller-owned shared RuntimeExecutionGuard")
        if not isinstance(recovery_bridge, GraphRunRecoveryBridge):
            raise TypeError("recovery_bridge must reuse the canonical GraphRunRecoveryBridge")
        if not isinstance(intent_digest, str) or len(intent_digest) != 64:
            raise ValueError("intent_digest must be a SHA-256 digest")
        try:
            int(intent_digest, 16)
        except ValueError as exc:
            raise ValueError("intent_digest must be a SHA-256 digest") from exc
        self.coordinator = coordinator
        self.verifier = verifier
        self.journal = journal
        self.task_plan = task_plan
        self.intent_digest = intent_digest
        self.runtime_guard = runtime_guard
        if recovery_bridge.plan.digest() != task_plan.digest():
            raise ValueError("recovery bridge is bound to a different TaskPlan")
        if recovery_bridge.intent_digest != intent_digest or recovery_bridge.source_revision != coordinator.base_revision:
            raise ValueError("recovery bridge intent or source revision does not match worker lifecycle")
        self.recovery_bridge = recovery_bridge
        self.recovery_bridge.start(budget_snapshot=self.runtime_guard.snapshot())
        self._prepared: dict[str, PreparedGraphWorker] = {}
        self._lock = RLock()

    @staticmethod
    def _binding(task_id: str, worker_id: str, plan_digest: str, intent_digest: str, stage: str) -> str:
        return json.dumps({
            "task_id": task_id, "worker_id": worker_id, "task_digest": plan_digest,
            "intent_digest": intent_digest, "stage": stage,
        }, sort_keys=True, separators=(",", ":"))

    def _budget_summary(self) -> dict[str, Any]:
        snapshot = self.runtime_guard.snapshot()
        return {
            "schema_version": snapshot.get("schema_version"),
            "max_steps": snapshot.get("max_steps"),
            "steps_used": snapshot.get("steps_used"),
            "max_attempts": snapshot.get("max_attempts"),
            "attempts_used": snapshot.get("attempts_used"),
            "remaining_seconds": snapshot.get("remaining_seconds"),
            "cancelled": snapshot.get("cancelled"),
            "cancellation_reason": snapshot.get("cancellation_reason"),
        }

    def _record_recovery_node(
        self,
        *,
        task_id: str,
        status: str,
        next_safe_action: str,
        worker_id: str | None = None,
        workspace_id: str | None = None,
        attempts: int | None = None,
        evidence_refs: Sequence[str] = (),
        blockers: Sequence[str] = (),
    ) -> dict[str, Any]:
        snapshot = self.recovery_bridge.load()
        node = snapshot["nodes"][task_id]
        payload = {
            "task_id": task_id, "status": status, "next_safe_action": next_safe_action,
            "worker_id": worker_id, "workspace_id": workspace_id, "attempts": attempts,
            "evidence_refs": list(evidence_refs), "blockers": list(blockers),
            "budget_digest": self.runtime_guard.snapshot()["digest"],
        }
        event_suffix = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()[:20]
        return self.recovery_bridge.record_node(
            event_id=f"{task_id}:{status}:{event_suffix}",
            node_id=task_id, status=status,
            budget_snapshot=self.runtime_guard.snapshot(),
            next_safe_action=next_safe_action,
            worker_id=worker_id, workspace_id=workspace_id,
            attempts=node["attempts"] if attempts is None else attempts,
            evidence_refs=evidence_refs, blockers=blockers,
        )

    def _block_recovery_run(self, reason: str, next_safe_action: str) -> None:
        try:
            snapshot = self.recovery_bridge.load()
            if snapshot.get("run_status") == "in_progress":
                suffix = hashlib.sha256(reason.encode("utf-8")).hexdigest()[:20]
                self.recovery_bridge.block_run(
                    event_id=f"lifecycle-blocked:{suffix}", reason=reason,
                    next_safe_action=next_safe_action,
                    budget_snapshot=self.runtime_guard.snapshot(),
                )
        except BaseException:
            # Recovery disagreement remains fail-closed; the caller still raises
            # the primary execution failure and ProgressJournal retains its record.
            pass

    @staticmethod
    def _receipt_digest(receipt: Mapping[str, Any]) -> str:
        payload = dict(receipt)
        supplied = payload.pop("receipt_digest", None)
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        if supplied != expected:
            raise GraphWorkerLifecycleError("verification receipt digest mismatch")
        return expected

    def prepare(
        self,
        *,
        task_id: str,
        worker_id: str,
        owned_paths: Sequence[str],
        worker_identity: str,
        execute_in_workspace: Callable[[Path, Mapping[str, Any]], Any],
        checks: Sequence[Mapping[str, Any]],
    ) -> PreparedGraphWorker:
        """Run a worker in its own worktree and return a digest-bound verified proposal."""
        if task_id not in self.task_plan.tasks:
            raise KeyError(f"task is not present in the canonical TaskPlan: {task_id}")
        task = self.task_plan.tasks[task_id]
        if task.status != "pending":
            raise PermissionError(f"task {task_id} is not pending")
        plan_digest = self.task_plan.digest()
        prior = self.journal.snapshot(task_id)
        recovery_snapshot = self.recovery_bridge.load()
        recovery_node = recovery_snapshot["nodes"][task_id]
        blockers = [dep for dep in task.dependencies if self.task_plan.tasks[dep].status != "done"]
        if blockers:
            if not prior.get("event_count", 0):
                if recovery_node["status"] == "waiting":
                    self._record_recovery_node(
                        task_id=task_id, status="blocked",
                        next_safe_action="complete_dependencies_then_reconcile",
                        blockers=[f"dependency:{dep}" for dep in blockers],
                    )
                self.journal.record(
                    task_id, "dependency-blocked", state="blocked", stage="dependency",
                    message=self._binding(task_id, worker_id, plan_digest, self.intent_digest, "dependency_blocked")
                    + "; blocked_by=" + json.dumps(blockers, separators=(",", ":"))
                    + "; next_action=complete_dependencies_then_reconcile",
                )
            raise PermissionError(f"task {task_id} has unresolved dependencies: {blockers}")
        reconciled_queue = (
            prior.get("state") == "queued"
            and prior.get("stage") == "dependency_reconciliation"
            and "dispatch_authorized" in prior.get("message", "")
        )
        if prior.get("event_count", 0) and not reconciled_queue:
            if recovery_node["status"] == "working":
                self._record_recovery_node(
                    task_id=task_id, status="unknown",
                    next_safe_action="reconcile_worker_workspace_and_receipt_before_resume",
                    worker_id=worker_id,
                    evidence_refs=[prior["evidence_ref"]] if prior.get("evidence_ref") else (),
                )
                self._block_recovery_run("prior progress exists without an in-memory worker lease", "reconcile_worker_workspace_and_receipt_before_resume")
            elif recovery_node["status"] == "waiting":
                self._record_recovery_node(
                    task_id=task_id, status="blocked",
                    next_safe_action="inspect_prior_progress_before_redispatch",
                    blockers=["prior_progress_requires_reconciliation"],
                )
            self.journal.record(
                task_id, "duplicate-dispatch-blocked", state="blocked", stage="recovery",
                message=self._binding(task_id, worker_id, plan_digest, self.intent_digest, "inspect_prior_state"),
                evidence_ref=prior.get("evidence_ref"),
            )
            raise PermissionError("durable progress already exists; reconcile prior worker state before redispatch")
        if recovery_node["status"] != "waiting":
            raise PermissionError(f"recovery ledger node is {recovery_node['status']}; explicit reconciliation is required")
        if task.metadata.get("execution_mode") not in {"mutating", "write"}:
            raise PermissionError("isolated workspace lifecycle only accepts explicitly mutating TaskPlan tasks")
        declared, requested = set(task.files), set(owned_paths)
        if not declared or not requested or not requested.issubset(declared):
            raise PermissionError("worker ownership must be a non-empty subset of TaskPlan-declared files")
        proposal: WorkspaceProposal | None = None
        receipt_path: Path | None = None
        workspace: Path | None = None
        try:
            self.runtime_guard.check()
            self.runtime_guard.charge_step()
            self.runtime_guard.charge_attempt(f"graph-worker:{task_id}:{worker_id}")
            self.journal.record(
                task_id, "worker-started", state="running", stage="isolated_workspace",
                message=self._binding(task_id, worker_id, plan_digest, self.intent_digest, "worker_started")
                + "; budget=" + json.dumps(self._budget_summary(), sort_keys=True, separators=(",", ":")),
            )
            workspace = self.coordinator.create_worker(worker_id, owned_paths)
            recovery_state = self.recovery_bridge.load()["nodes"][task_id]
            self._record_recovery_node(
                task_id=task_id, status="working", next_safe_action="execute_worker_and_capture_proposal",
                worker_id=worker_id, workspace_id=str(workspace), attempts=recovery_state["attempts"] + 1,
            )
            self.journal.record(
                task_id, "workspace-created", state="running", stage="isolated_workspace",
                message=self._binding(task_id, worker_id, plan_digest, self.intent_digest, "workspace_created")
                + "; base_revision=" + self.coordinator.base_revision
                + "; dependencies=" + json.dumps(task.dependencies, separators=(",", ":"))
                + "; next_action=execute_worker_and_capture_proposal",
                evidence_ref=str(workspace),
            )
            worker_context = dict(task.__dict__)
            worker_context["_auren_cancellation_event"] = self.runtime_guard.cancellation_event
            worker_context["_auren_runtime_guard"] = self.runtime_guard
            execute_in_workspace(workspace, worker_context)
            self.runtime_guard.check()
            proposal = self.coordinator.prepare_proposal(worker_id)
            self.journal.record(
                task_id, "proposal-captured", state="running", stage="independent_verification",
                message=self._binding(task_id, worker_id, plan_digest, self.intent_digest, "proposal_captured")
                + "; budget=" + json.dumps(self.runtime_guard.snapshot(), sort_keys=True, separators=(",", ":")),
                evidence_ref=proposal.artifact_path,
            )
            self.runtime_guard.check()
            remaining = self.runtime_guard.snapshot().get("remaining_seconds")
            if not checks:
                raise ValueError("independent verification checks are required")
            per_check_budget = None
            if remaining is not None:
                remaining_budget = int(remaining)
                if remaining_budget < 1 or len(checks) > remaining_budget:
                    raise ExecutionBudgetExceeded("shared task deadline cannot cover all verification checks")
                per_check_budget = max(1, remaining_budget // len(checks))
            bounded_checks = []
            for check in checks:
                row = dict(check)
                if per_check_budget is not None:
                    row["timeout_seconds"] = min(
                        row.get("timeout_seconds", min(120, self.verifier.max_timeout_seconds)),
                        per_check_budget,
                    )
                bounded_checks.append(row)
            receipt = self.verifier.verify(
                proposal, worker_identity=worker_identity, task_digest=plan_digest,
                intent_digest=self.intent_digest, checks=bounded_checks,
            )
            self.runtime_guard.check()
            expected = {
                "status": "passed", "worker_identity": worker_identity,
                "task_digest": plan_digest, "intent_digest": self.intent_digest,
                "source_revision": proposal.base_revision,
                "artifact_digest": proposal.artifact_digest,
                "verified_paths": list(sorted(proposal.paths)),
            }
            for key, value in expected.items():
                if receipt.get(key) != value:
                    raise GraphWorkerLifecycleError(f"verification receipt binding mismatch: {key}")
            receipt_digest = self._receipt_digest(receipt)
            receipt_path = Path(proposal.artifact_path).with_suffix(".verification.json")
            if receipt_path.exists() or receipt_path.is_symlink():
                raise PermissionError("verification receipt path already exists or is unsafe")
            temp_path = receipt_path.with_name(receipt_path.name + ".tmp")
            if temp_path.exists() or temp_path.is_symlink():
                raise PermissionError("temporary verification receipt path already exists or is unsafe")
            payload = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
            fd = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, receipt_path)
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
            persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
            if persisted != dict(receipt) or self._receipt_digest(persisted) != receipt_digest:
                raise GraphWorkerLifecycleError("persisted verification receipt failed read-back validation")
            prepared = PreparedGraphWorker(
                task_id, worker_id, plan_digest, self.intent_digest, proposal,
                dict(receipt), str(receipt_path), str(workspace),
            )
            with self._lock:
                if worker_id in self._prepared:
                    raise ValueError("worker already has a prepared proposal")
                self._prepared[worker_id] = prepared
            self.journal.record(
                task_id, "proposal-independently-verified", state="running", stage="adoption",
                message=self._binding(task_id, worker_id, plan_digest, self.intent_digest, "await_serialized_adoption")
                + "; budget=" + json.dumps(self.runtime_guard.snapshot(), sort_keys=True, separators=(",", ":")),
                evidence_ref=str(receipt_path),
            )
            return prepared
        except BaseException as exc:
            try:
                self.coordinator.cancel_siblings(f"graph worker preparation failed: {type(exc).__name__}")
            except BaseException:
                pass
            try:
                self.journal.record(
                    task_id, "worker-preparation-failed", state="failed", stage="recovery",
                    message=self._binding(task_id, worker_id, plan_digest, self.intent_digest, "inspect_failed_workspace")
                    + "; error=" + type(exc).__name__,
                    evidence_ref=str(receipt_path or (proposal.artifact_path if proposal else "")) or None,
                )
            except BaseException:
                pass
            raise

    def reconcile_dependencies(self, task_id: str) -> dict[str, Any]:
        """Reconcile a durable dependency blocker only after all prerequisites are done."""
        if task_id not in self.task_plan.tasks:
            raise KeyError(f"task is not present in the canonical TaskPlan: {task_id}")
        task = self.task_plan.tasks[task_id]
        prior = self.journal.snapshot(task_id)
        if prior.get("state") != "blocked" or prior.get("stage") != "dependency" or "dependency_blocked" not in prior.get("message", ""):
            raise PermissionError("only a recorded dependency blocker can be reconciled")
        blockers = [dep for dep in task.dependencies if self.task_plan.tasks[dep].status != "done"]
        if blockers:
            raise PermissionError(f"dependencies remain incomplete: {blockers}")
        recovery = self.recovery_bridge.load()
        node = recovery["nodes"][task_id]
        if node["status"] != "blocked":
            raise PermissionError("recovery ledger has no blocked node to reconcile")
        self._record_recovery_node(
            task_id=task_id, status="waiting", next_safe_action="dispatch_worker",
            attempts=node["attempts"] + 1,
        )
        return self.journal.record_reconciliation(
            task_id, state="queued", stage="dependency_reconciliation",
            message=self._binding(task_id, "reconciled", self.task_plan.digest(), self.intent_digest, "dispatch_authorized")
            + "; next_action=dispatch_worker",
            evidence_ref=prior.get("evidence_ref"),
            expected_sequence=prior["sequence"],
        ).__dict__

    def adopt_prepared(self, worker_ids: Sequence[str]) -> dict[str, Any]:
        """Adopt all prepared siblings in one checked apply, then record truthful completion."""
        if not worker_ids or len(set(worker_ids)) != len(worker_ids):
            raise ValueError("a non-empty unique worker id list is required")
        with self._lock:
            missing = [worker_id for worker_id in worker_ids if worker_id not in self._prepared]
            if missing:
                raise PermissionError(f"prepared worker receipts are unavailable; reconcile before adoption: {missing}")
            prepared = [self._prepared[worker_id] for worker_id in worker_ids]
        adoption_applied = False
        try:
            self.runtime_guard.check()
            for item in prepared:
                task = self.task_plan.tasks.get(item.task_id)
                if task is None or task.status != "pending":
                    raise PermissionError(f"task state changed before adoption: {item.task_id}")
                if self.task_plan.digest() != item.task_digest or self.intent_digest != item.intent_digest:
                    raise PermissionError("TaskPlan or intent changed after worker verification")
                receipt = dict(item.receipt)
                if self._receipt_digest(receipt) != receipt.get("receipt_digest"):
                    raise PermissionError("verification receipt changed before adoption")
                if receipt.get("status") != "passed" or receipt.get("artifact_digest") != item.proposal.artifact_digest:
                    raise PermissionError("unverified proposal cannot be adopted")
                persisted = Path(item.receipt_path)
                if persisted.is_symlink() or not persisted.is_file():
                    raise PermissionError("verification receipt is missing or unsafe")
                try:
                    persisted_receipt = json.loads(persisted.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise PermissionError("persisted verification receipt is invalid") from exc
                if persisted_receipt != receipt:
                    raise PermissionError("persisted verification receipt does not match prepared result")
            self.runtime_guard.check()
            adopted = self.coordinator.adopt_many([item.proposal for item in prepared])
            adoption_applied = True
            for item in prepared:
                if tuple(adopted[item.worker_id]) != tuple(sorted(item.proposal.paths)):
                    raise GraphWorkerLifecycleError("adopted paths differ from verified proposal")
            # Complete cleanup for the whole sibling set before any task is marked
            # complete. A cleanup failure leaves all journal tasks non-terminal.
            for item in prepared:
                self.coordinator.cleanup_worker(item.worker_id)
            results = {}
            for item in prepared:
                task = self.task_plan.tasks[item.task_id]
                paths = adopted[item.worker_id]
                recovery_node = self.recovery_bridge.load()["nodes"][item.task_id]
                self._record_recovery_node(
                    task_id=item.task_id, status="complete",
                    next_safe_action="verify_graph_receipt_and_stop",
                    worker_id=item.worker_id, workspace_id=item.workspace_id,
                    attempts=recovery_node["attempts"],
                    evidence_refs=(item.proposal.artifact_path, item.receipt_path),
                )
                self.journal.record(
                    item.task_id, "verified-proposal-adopted", state="completed", stage="adoption",
                    message=self._binding(item.task_id, item.worker_id, item.task_digest, item.intent_digest, "complete"),
                    completed_units=1, total_units=1, evidence_ref=item.receipt_path,
                )
                task.status = "done"
                results[item.worker_id] = {
                    "status": "completed", "task_id": item.task_id, "worker_id": item.worker_id,
                    "task_digest": item.task_digest, "intent_digest": item.intent_digest,
                    "source_revision": item.proposal.base_revision,
                    "artifact_digest": item.proposal.artifact_digest,
                    "receipt_digest": item.receipt["receipt_digest"], "receipt_path": item.receipt_path,
                    "adopted_paths": list(paths), "external_evaluator_independence": "not_proven",
                }
            return {"status": "completed", "workers": results}
        except BaseException as exc:
            try:
                self.runtime_guard.cancel(f"graph adoption lifecycle failed: {type(exc).__name__}")
                self.coordinator.cancel_siblings(f"graph adoption lifecycle failed: {type(exc).__name__}")
            except BaseException:
                pass
            for item in prepared:
                try:
                    recovery_snapshot = self.recovery_bridge.load()
                    node = recovery_snapshot["nodes"][item.task_id]
                    if node["status"] == "working":
                        self._record_recovery_node(
                            task_id=item.task_id, status="unknown" if adoption_applied else "failed",
                            next_safe_action="reconcile_target_tree_and_recovery_ledger" if adoption_applied else "inspect_failed_worker_and_retry_only_after_reconciliation",
                            worker_id=item.worker_id, workspace_id=item.workspace_id,
                            attempts=node["attempts"],
                            evidence_refs=(item.proposal.artifact_path, item.receipt_path),
                        )
                except BaseException:
                    pass
                try:
                    self.journal.record(
                        item.task_id, "adoption-failed", state="failed", stage="recovery",
                        message=self._binding(item.task_id, item.worker_id, item.task_digest, item.intent_digest, "reconcile_adoption")
                        + "; error=" + type(exc).__name__,
                        evidence_ref=item.receipt_path,
                    )
                except BaseException:
                    pass
            self._block_recovery_run(
                f"graph adoption lifecycle failed: {type(exc).__name__}",
                "reconcile_target_tree_and_recovery_ledger",
            )
            raise

    def run(self, **kwargs: Any) -> dict[str, Any]:
        """Convenience for a single worker; sibling graphs should use prepare + one adoption node."""
        prepared = self.prepare(**kwargs)
        return self.adopt_prepared((prepared.worker_id,))

    def as_prepare_node(self, *, prepared_workers: dict[str, PreparedGraphWorker], **kwargs: Any) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
        """Create a StateGraph worker node; the canonical graph owns scheduling."""
        worker_id = kwargs["worker_id"]
        def node(_state: Mapping[str, Any]) -> Mapping[str, Any]:
            prepared = self.prepare(**kwargs)
            prepared_workers[worker_id] = prepared
            return {
                "graph_worker_prepared_" + worker_id: {
                    "worker_id": worker_id, "task_id": prepared.task_id,
                    "task_digest": prepared.task_digest, "intent_digest": prepared.intent_digest,
                    "artifact_digest": prepared.proposal.artifact_digest,
                    "receipt_digest": prepared.receipt["receipt_digest"],
                    "receipt_path": prepared.receipt_path, "status": "verified",
                }
            }
        return node

    def as_adoption_node(self, *, prepared_workers: dict[str, PreparedGraphWorker], worker_ids: Sequence[str]) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
        """Create the single serialized adoption node after all worker dependencies."""
        ids = tuple(worker_ids)
        def node(state: Mapping[str, Any]) -> Mapping[str, Any]:
            for worker_id in ids:
                marker = state.get("graph_worker_prepared_" + worker_id)
                if not isinstance(marker, Mapping) or marker.get("status") != "verified":
                    raise PermissionError(f"worker {worker_id} lacks a verified graph result")
            missing = [worker_id for worker_id in ids if worker_id not in prepared_workers]
            if missing:
                self.runtime_guard.cancel("prepared graph worker state was lost during restart")
                self.coordinator.cancel_siblings("prepared graph worker state was lost during restart")
                for worker_id in missing:
                    marker = state.get("graph_worker_prepared_" + worker_id)
                    task_id = marker.get("task_id") if isinstance(marker, Mapping) else None
                    if isinstance(task_id, str) and task_id in self.task_plan.tasks:
                        try:
                            snapshot = self.journal.snapshot(task_id)
                            if snapshot.get("state") not in {"completed", "failed", "cancelled"}:
                                self.journal.record(
                                    task_id, "prepared-worker-lost", state="blocked", stage="recovery",
                                    message=self._binding(
                                        task_id, worker_id, self.task_plan.digest(), self.intent_digest,
                                        "prepared_worker_lost",
                                    ) + "; next_action=inspect_persisted_receipt_and_workspace",
                                    evidence_ref=snapshot.get("evidence_ref"),
                                )
                        except BaseException:
                            pass
                raise PermissionError("prepared worker state was lost; restart reconciliation is required")
            return {"graph_worker_adoption": self.adopt_prepared(ids)}
        return node

    def as_node(self, **kwargs: Any) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
        """Return a single-worker StateGraph node; StateGraph remains the scheduler."""
        def node(_state: Mapping[str, Any]) -> Mapping[str, Any]:
            return {"graph_worker_lifecycle": self.run(**kwargs)}
        return node


__all__ = ["GraphWorkerLifecycle", "GraphWorkerLifecycleError", "PreparedGraphWorker"]
