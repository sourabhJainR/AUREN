"""StateGraph node adapter for isolated mutation, independent verification, and durable progress.

This module is a worker lifecycle adapter, not a scheduler. TaskPlan and StateGraph
remain the only task planner and scheduler; the coordinator serializes adoption.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from portable.graph_workspace_adoption import GraphWorkspaceCoordinator, WorkspaceProposal
from portable.independent_graph_verification import IndependentGraphVerifier
from portable.progress_journal import ProgressJournal
from portable.task_planner import TaskPlan


class GraphWorkerLifecycleError(RuntimeError):
    """A graph worker could not produce independently verified adoptable work."""


class GraphWorkerLifecycle:
    """Run one mutating graph node through isolated workspace and verified adoption."""

    def __init__(
        self,
        *,
        coordinator: GraphWorkspaceCoordinator,
        verifier: IndependentGraphVerifier,
        journal: ProgressJournal,
        task_plan: TaskPlan,
        intent_digest: str,
    ) -> None:
        if not isinstance(coordinator, GraphWorkspaceCoordinator):
            raise TypeError("coordinator must be the canonical GraphWorkspaceCoordinator")
        if not isinstance(verifier, IndependentGraphVerifier):
            raise TypeError("verifier must be an IndependentGraphVerifier")
        if not isinstance(journal, ProgressJournal):
            raise TypeError("journal must be the canonical ProgressJournal")
        if not isinstance(task_plan, TaskPlan):
            raise TypeError("task_plan must be the canonical TaskPlan")
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

    @staticmethod
    def _record_binding(task_id: str, worker_id: str, plan_digest: str, intent_digest: str, stage: str) -> str:
        return json.dumps({
            "task_id": task_id,
            "worker_id": worker_id,
            "task_digest": plan_digest,
            "intent_digest": intent_digest,
            "stage": stage,
        }, sort_keys=True, separators=(",", ":"))

    def run(
        self,
        *,
        task_id: str,
        worker_id: str,
        owned_paths: Sequence[str],
        worker_identity: str,
        execute_in_workspace: Callable[[Path, Mapping[str, Any]], Any],
        checks: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Execute one mutating node; never equate worker output with verified success."""
        if task_id not in self.task_plan.tasks:
            raise KeyError(f"task is not present in the canonical TaskPlan: {task_id}")
        task = self.task_plan.tasks[task_id]
        if task.status != "pending":
            raise PermissionError(f"task {task_id} is not pending")
        if not all(self.task_plan.tasks[dep].status == "done" for dep in task.dependencies):
            raise PermissionError(f"task {task_id} has unresolved dependencies")
        if task.metadata.get("execution_mode") not in {"mutating", "write"}:
            raise PermissionError("isolated workspace lifecycle only accepts explicitly mutating TaskPlan tasks")
        declared = set(task.files)
        requested = set(owned_paths)
        if not declared or not requested or not requested.issubset(declared):
            raise PermissionError("worker ownership must be a non-empty subset of TaskPlan-declared files")
        plan_digest = self.task_plan.digest()
        prior = self.journal.snapshot(task_id)
        if prior.get("event_count", 0):
            self.journal.record(
                task_id, "duplicate-dispatch-blocked", state="blocked", stage="recovery",
                message=self._record_binding(task_id, worker_id, plan_digest, self.intent_digest, "inspect_prior_state"),
                evidence_ref=prior.get("evidence_ref"),
            )
            raise PermissionError("durable progress already exists; reconcile prior worker state before redispatch")
        self.journal.record(
            task_id, "worker-started", state="running", stage="isolated_workspace",
            message=self._record_binding(task_id, worker_id, plan_digest, self.intent_digest, "worker_started"),
        )
        workspace: Path | None = None
        proposal: WorkspaceProposal | None = None
        receipt_path: Path | None = None
        try:
            workspace = self.coordinator.create_worker(worker_id, owned_paths)
            execute_in_workspace(workspace, task.__dict__.copy())
            proposal = self.coordinator.prepare_proposal(worker_id)
            self.journal.record(
                task_id, "proposal-captured", state="running", stage="independent_verification",
                message=self._record_binding(task_id, worker_id, plan_digest, self.intent_digest, "proposal_captured"),
                evidence_ref=proposal.artifact_path,
            )
            receipt = self.verifier.verify(
                proposal,
                worker_identity=worker_identity,
                task_digest=plan_digest,
                intent_digest=self.intent_digest,
                checks=checks,
            )
            expected = {
                "status": "passed",
                "worker_identity": worker_identity,
                "task_digest": plan_digest,
                "intent_digest": self.intent_digest,
                "source_revision": proposal.base_revision,
                "artifact_digest": proposal.artifact_digest,
                "verified_paths": list(sorted(proposal.paths)),
            }
            for key, value in expected.items():
                if receipt.get(key) != value:
                    raise GraphWorkerLifecycleError(f"verification receipt binding mismatch: {key}")
            receipt_digest = receipt.get("receipt_digest")
            if not isinstance(receipt_digest, str) or len(receipt_digest) != 64:
                raise GraphWorkerLifecycleError("verification receipt digest is missing")
            receipt_path = Path(proposal.artifact_path).with_suffix(".verification.json")
            if receipt_path.exists() or receipt_path.is_symlink():
                raise PermissionError("verification receipt path already exists or is unsafe")
            payload = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
            temp_path = receipt_path.with_name(receipt_path.name + ".tmp")
            if temp_path.exists() or temp_path.is_symlink():
                raise PermissionError("temporary verification receipt path already exists or is unsafe")
            fd = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, receipt_path)
            except BaseException:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            if hashlib.sha256(receipt_path.read_bytes()).hexdigest() == "":
                raise GraphWorkerLifecycleError("verification receipt persistence failed")
            adopted = self.coordinator.adopt(proposal)
            if tuple(adopted) != tuple(sorted(proposal.paths)):
                raise GraphWorkerLifecycleError("adopted paths differ from verified proposal")
            task.status = "done"
            self.journal.record(
                task_id, "verified-proposal-adopted", state="completed", stage="adoption",
                message=self._record_binding(task_id, worker_id, plan_digest, self.intent_digest, "complete"),
                completed_units=1, total_units=1, evidence_ref=str(receipt_path),
            )
            self.coordinator.cleanup_worker(worker_id)
            return {
                "status": "completed",
                "task_id": task_id,
                "worker_id": worker_id,
                "task_digest": plan_digest,
                "intent_digest": self.intent_digest,
                "source_revision": proposal.base_revision,
                "artifact_digest": proposal.artifact_digest,
                "receipt_digest": receipt_digest,
                "receipt_path": str(receipt_path),
                "adopted_paths": list(adopted),
                "external_evaluator_independence": "not_proven",
            }
        except BaseException as exc:
            try:
                self.coordinator.cancel_siblings(
                    f"graph worker lifecycle failed: {type(exc).__name__}",
                )
            except BaseException:
                pass
            try:
                self.journal.record(
                    task_id, "worker-lifecycle-failed", state="failed", stage="recovery",
                    message=self._record_binding(task_id, worker_id, plan_digest, self.intent_digest, "inspect_failed_workspace")
                    + "; error=" + type(exc).__name__,
                    evidence_ref=str(receipt_path or (proposal.artifact_path if proposal else "")) or None,
                )
            except BaseException:
                # The primary failure is still raised. A damaged/unavailable journal
                # must never be converted into successful completion.
                pass
            raise

    def as_node(
        self,
        *,
        task_id: str,
        worker_id: str,
        owned_paths: Sequence[str],
        worker_identity: str,
        execute_in_workspace: Callable[[Path, Mapping[str, Any]], Any],
        checks: Sequence[Mapping[str, Any]],
    ) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
        """Return a StateGraph node callable; StateGraph remains the scheduler."""
        def node(_state: Mapping[str, Any]) -> Mapping[str, Any]:
            result = self.run(
                task_id=task_id,
                worker_id=worker_id,
                owned_paths=owned_paths,
                worker_identity=worker_identity,
                execute_in_workspace=execute_in_workspace,
                checks=checks,
            )
            return {"graph_worker_lifecycle": result}
        return node


__all__ = ["GraphWorkerLifecycle", "GraphWorkerLifecycleError"]
