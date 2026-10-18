"""Bridge versioned engineering plans to AUREN's canonical recovery ledger.

The plan store holds full plan/run records with CAS revisions. The recovery ledger
stores an integrity-bound pointer alongside runtime checkpoints. Cross-database
writes cannot be atomic, so mismatches fail closed and require explicit reconciliation.
"""
from __future__ import annotations

from typing import Any, Mapping

from .engineering_plan import EngineeringPlan, EngineeringPlanRun, TaskProgress, new_plan_run
from .engineering_plan_store import EngineeringPlanStore
from .engineering_recovery import EngineeringRecoveryLedger


class EngineeringPlanCheckpointMismatch(RuntimeError):
    """Persisted plan/run state disagrees with its canonical recovery checkpoint."""


class EngineeringPlanRecoveryBridge:
    PHASE = "engineering_plan"

    def __init__(self, *, store: EngineeringPlanStore, ledger: EngineeringRecoveryLedger) -> None:
        self.store = store
        self.ledger = ledger

    @staticmethod
    def _intent(plan: EngineeringPlan) -> str:
        return f"engineering-plan:{plan.run_id}:{plan.digest}"

    def _checkpoint_payload(
        self, plan: EngineeringPlan, run: EngineeringPlanRun, *, next_safe_action: str
    ) -> dict[str, Any]:
        return {
            "contract": "auren-engineering-plan-checkpoint-v1",
            "plan_digest": plan.digest,
            "run_digest": run.digest,
            "run_revision": run.revision,
            "source_revision": plan.source_revision,
            "store_path": str(self.store.database_path.resolve()),
            "next_safe_action": next_safe_action,
        }

    def start(self, plan: EngineeringPlan) -> EngineeringPlanRun:
        """Create or validate a plan, then bind it to the canonical recovery ledger."""
        created = False
        try:
            stored_plan, run = self.store.load(plan.run_id)
        except KeyError:
            run = self.store.create(plan, new_plan_run(plan))
            stored_plan = plan
            created = True
        if stored_plan.digest != plan.digest:
            raise ValueError("run_id already belongs to a different immutable engineering plan")
        self.ledger.start_run(plan.run_id, self._intent(plan))
        existing = self.ledger.get_checkpoint(plan.run_id, self.PHASE)
        payload = self._checkpoint_payload(stored_plan, run, next_safe_action="schedule_dependency_ready_task")
        if existing is None and not created:
            raise EngineeringPlanCheckpointMismatch(
                "stored plan exists but its canonical recovery checkpoint is missing; explicit reconciliation is required"
            )
        if existing is None:
            self.ledger.checkpoint(
                plan.run_id, self.PHASE, status="in_progress",
                head_sha=plan.source_revision, payload=payload,
            )
        else:
            self._verify_checkpoint(stored_plan, run, existing.payload)
        return run

    def load_verified(self, run_id: str) -> tuple[EngineeringPlan, EngineeringPlanRun]:
        """Load full state only if the recovery ledger pointer exactly matches it."""
        plan, run = self.store.load(run_id)
        checkpoint = self.ledger.get_checkpoint(run_id, self.PHASE)
        if checkpoint is None:
            raise EngineeringPlanCheckpointMismatch("canonical recovery checkpoint is missing")
        self._verify_checkpoint(plan, run, checkpoint.payload)
        if checkpoint.status in {"blocked", "rolled_back"}:
            raise EngineeringPlanCheckpointMismatch(
                f"plan checkpoint is {checkpoint.status}; explicit recovery decision is required"
            )
        return plan, run

    def _verify_checkpoint(
        self, plan: EngineeringPlan, run: EngineeringPlanRun, payload: Mapping[str, Any]
    ) -> None:
        expected = self._checkpoint_payload(plan, run, next_safe_action=str(payload.get("next_safe_action", "")))
        for key in ("contract", "plan_digest", "run_digest", "run_revision", "source_revision", "store_path"):
            if payload.get(key) != expected[key]:
                raise EngineeringPlanCheckpointMismatch(
                    f"plan checkpoint mismatch for {key}; refusing implicit resume"
                )

    def transition_task(
        self, run_id: str, task_id: str, target: str, *, reason: str,
        evidence_refs: tuple[str, ...] = (), replan_generation: int | None = None,
        next_safe_action: str = "recompute_dependency_ready_tasks",
    ) -> EngineeringPlanRun:
        """Persist one CAS transition, then publish its digest to the recovery ledger.

        If the second write fails, subsequent loads detect the stale pointer and stop;
        the bridge never silently guesses which database write should win.
        """
        plan, current = self.load_verified(run_id)
        updated = current.transition_task(
            task_id, target, plan=plan, reason=reason, evidence_refs=evidence_refs,
            replan_generation=replan_generation,
        )
        self.store.save_run(run_id, updated, expected_revision=current.revision)
        all_passed = all(item.status == "passed" for item in updated.tasks)
        payload = self._checkpoint_payload(plan, updated, next_safe_action=next_safe_action)
        if all_passed:
            evidence = sorted({ref for item in updated.tasks for ref in item.evidence_refs})
            self.ledger.checkpoint(
                run_id, self.PHASE, status="verified", head_sha=plan.source_revision,
                payload={
                    **payload,
                    "verified_head_sha": plan.source_revision,
                    "verification_evidence": evidence,
                },
            )
        else:
            self.ledger.checkpoint(
                run_id, self.PHASE, status="in_progress",
                head_sha=plan.source_revision, payload=payload,
            )
        return updated

    def reconcile_checkpoint(self, run_id: str, *, reason: str, expected_plan_digest: str) -> EngineeringPlanRun:
        """Explicitly republish a checkpoint after an operator inspects a split write.

        This is not automatic recovery: callers must provide the expected plan digest
        and a non-empty rationale. It never alters the plan store or task progress.
        """
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reconciliation reason is required")
        plan, run = self.store.load(run_id)
        if plan.digest != expected_plan_digest:
            raise EngineeringPlanCheckpointMismatch("operator-supplied plan digest does not match stored plan")
        self.ledger.start_run(run_id, self._intent(plan))
        existing = self.ledger.get_checkpoint(run_id, self.PHASE)
        evidence = sorted({ref for item in run.tasks for ref in item.evidence_refs})
        payload = {
            **self._checkpoint_payload(plan, run, next_safe_action="operator_reconciled_checkpoint"),
            "reconciliation_reason": reason.strip(),
        }
        if all(item.status == "passed" for item in run.tasks):
            payload.update({"verified_head_sha": plan.source_revision, "verification_evidence": evidence})
            status = "verified"
        else:
            status = "in_progress"
        if existing is not None and existing.status == "rolled_back":
            raise EngineeringPlanCheckpointMismatch("rolled-back plan cannot be reconciled into an active run")
        self.ledger.checkpoint(
            run_id, self.PHASE, status=status, head_sha=plan.source_revision, payload=payload,
        )
        return run
