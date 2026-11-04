"""Evidence-gated continuation for one durable engineering plan goal.

The reconciler owns no task queue or authority. It reads the canonical plan and
ExecutionController, then dispatches only through TrustedPlanDispatchRoot.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
from typing import Any

from .engineering_context_packet import build_context_packet
from .engineering_plan import EngineeringPlan, PlanTask
from .engineering_plan_recovery import EngineeringPlanRecoveryBridge
from .execution_controller import ExecutionController
from .trusted_plan_dispatch import TrustedPlanDispatchRoot


class EngineeringPlanGoalReconciler:
    """Reconcile one plan run and dispatch its next dependency-ready unit.

    The instance is stateless between calls. Stable work keys and the canonical
    controller database recover submissions after lost acknowledgements/restart.
    Completion requires a host verifier to return explicit evidence references.
    """

    def __init__(
        self,
        *,
        run_id: str,
        controller: ExecutionController,
        recovery_bridge: EngineeringPlanRecoveryBridge,
        trusted_dispatch_root: TrustedPlanDispatchRoot,
        handler_resolver: Callable[[EngineeringPlan, PlanTask], str],
        payload_resolver: Callable[[EngineeringPlan, PlanTask], Mapping[str, Any]],
        budget_resolver: Callable[[EngineeringPlan, Any, PlanTask], Mapping[str, int]],
        completion_verifier: Callable[[EngineeringPlan, PlanTask, Any], Mapping[str, Any]],
        policy_constraints: Sequence[str],
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 128:
            raise ValueError("run_id must be non-empty text of at most 128 characters")
        if not isinstance(controller, ExecutionController):
            raise TypeError("controller must be the canonical ExecutionController")
        if not isinstance(recovery_bridge, EngineeringPlanRecoveryBridge):
            raise TypeError("recovery_bridge must be EngineeringPlanRecoveryBridge")
        if not isinstance(trusted_dispatch_root, TrustedPlanDispatchRoot):
            raise TypeError("trusted_dispatch_root must be TrustedPlanDispatchRoot")
        if trusted_dispatch_root.controller is not controller:
            raise ValueError("trusted dispatch root must use the same canonical controller")
        if trusted_dispatch_root.store is not recovery_bridge.store:
            raise ValueError("trusted dispatch root and recovery bridge must share the canonical plan store")
        if trusted_dispatch_root.recovery_bridge is not recovery_bridge:
            raise ValueError("trusted dispatch root must use the same recovery bridge")
        if not callable(getattr(controller, "task_by_work_key", None)):
            raise ValueError("controller must support canonical durable work-key lookup")
        for callback, name in (
            (handler_resolver, "handler_resolver"),
            (payload_resolver, "payload_resolver"),
            (budget_resolver, "budget_resolver"),
            (completion_verifier, "completion_verifier"),
        ):
            if not callable(callback):
                raise ValueError(f"{name} must be callable")
        if isinstance(policy_constraints, (str, bytes)):
            raise ValueError("policy_constraints must be a non-empty sequence of strings")
        constraints = tuple(policy_constraints)
        if not constraints or any(not isinstance(item, str) or not item.strip() for item in constraints):
            raise ValueError("policy_constraints must contain non-empty strings")

        self.run_id = run_id.strip()
        self.controller = controller
        self.recovery_bridge = recovery_bridge
        self.trusted_dispatch_root = trusted_dispatch_root
        self.handler_resolver = handler_resolver
        self.payload_resolver = payload_resolver
        self.budget_resolver = budget_resolver
        self.completion_verifier = completion_verifier
        self.policy_constraints = tuple(item.strip() for item in constraints)

    @property
    def goal_key(self) -> str:
        digest = hashlib.sha256(self.run_id.encode("utf-8")).hexdigest()[:24]
        return f"AUREN:plan-goal:{digest}"

    def work_key(self, task: PlanTask, replan_generation: int) -> str:
        identity = f"{self.run_id}\0{task.task_id}\0{replan_generation}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"AUREN:plan:{digest}"

    @staticmethod
    def _verified_evidence(
        result: Any,
    ) -> tuple[str, ...] | None:
        if not isinstance(result, Mapping) or result.get("verified") is not True:
            return None
        refs = result.get("evidence_refs")
        if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
            return None
        evidence = tuple(ref for ref in refs if (
            isinstance(ref, str) and ref.strip() and len(ref) <= 500 and ref.isprintable()
        ))
        if not evidence or len(evidence) != len(refs):
            return None
        return tuple(dict.fromkeys(evidence))

    def _dispatch(
        self, plan: EngineeringPlan, run: Any, task: PlanTask, *, work_key: str,
    ) -> Mapping[str, Any]:
        progress = next(item for item in run.tasks if item.task_id == task.task_id)
        if progress.status == "pending":
            self.recovery_bridge.transition_task(
                self.run_id, task.task_id, "ready",
                reason="all declared dependencies have passed",
            )
            plan, run = self.recovery_bridge.load_verified(self.run_id)
            progress = next(item for item in run.tasks if item.task_id == task.task_id)
        if progress.status == "ready":
            self.recovery_bridge.transition_task(
                self.run_id, task.task_id, "running",
                reason="reserve the immutable plan/run revision before trusted dispatch",
            )
            plan, run = self.recovery_bridge.load_verified(self.run_id)
        elif progress.status != "running":
            raise RuntimeError(f"task {task.task_id} cannot dispatch from {progress.status!r}")

        task = next(item for item in plan.tasks if item.task_id == task.task_id)
        packet = build_context_packet(
            plan=plan,
            run=run,
            task_id=task.task_id,
            policy_constraints=self.policy_constraints,
            budget_remaining=self.budget_resolver(plan, run, task),
            next_safe_action=f"Execute {task.task_id} and verify its acceptance criteria",
        )
        handler = self.handler_resolver(plan, task)
        if not isinstance(handler, str) or not handler.strip():
            raise ValueError("handler_resolver must return a registered handler name")
        payload = self.payload_resolver(plan, task)
        if not isinstance(payload, Mapping):
            raise ValueError("payload_resolver must return a mapping")
        durable = self.trusted_dispatch_root.dispatch(
            run_id=self.run_id,
            task_id=task.task_id,
            dispatch_key=work_key,
            handler=handler.strip(),
            packet=packet,
            payload=payload,
            goal=plan.goal,
        )
        recovered = self.controller.task_by_work_key(work_key)
        if recovered is None or recovered.id != durable.id:
            raise RuntimeError("trusted dispatch acknowledgement has no matching canonical work-key record")
        return {
            "status": "in_progress",
            "dispatched_task_id": durable.id,
            "task_id": task.task_id,
            "dispatch_key": work_key,
            "next_action": f"wait_for_task:{task.task_id}",
        }

    def __call__(self) -> Mapping[str, Any]:
        """Reconcile completed work, then dispatch one next eligible unit."""
        for _ in range(2 * 64):
            plan, run = self.recovery_bridge.load_verified(self.run_id)
            progress_by_id = {item.task_id: item for item in run.tasks}
            if all(item.status == "passed" for item in run.tasks):
                return {
                    "status": "complete",
                    "run_id": self.run_id,
                    "completed_task_ids": sorted(progress_by_id),
                    "next_action": "parent_goal_acceptance_verified",
                }

            active = next((
                task for task in plan.tasks
                if progress_by_id[task.task_id].status in {"running", "verifying"}
            ), None)
            if active is not None:
                progress = progress_by_id[active.task_id]
                key = self.work_key(active, progress.replan_generation)
                durable = self.controller.task_by_work_key(key)
                if durable is None:
                    if progress.status == "running":
                        return self._dispatch(plan, run, active, work_key=key)
                    return {
                        "status": "blocked",
                        "blocker": f"verifying task {active.task_id} has no canonical durable task record",
                        "next_action": "reconcile_task_identity_before_completion",
                    }
                if durable.state in {"pending", "running"}:
                    return {
                        "status": "in_progress",
                        "active_task_id": active.task_id,
                        "durable_task_id": durable.id,
                        "next_action": f"wait_for_task:{active.task_id}",
                    }
                if durable.state == "completed":
                    verification = self.completion_verifier(plan, active, durable)
                    evidence_refs = self._verified_evidence(verification)
                    if evidence_refs is None:
                        reason = (
                            verification.get("reason", "host completion evidence is not verified")
                            if isinstance(verification, Mapping)
                            else "host completion verifier returned an invalid result"
                        )
                        return {
                            "status": "waiting_external_dependency",
                            "blocker": str(reason)[:500],
                            "task_id": active.task_id,
                            "next_action": "obtain_and_verify_task_acceptance_evidence",
                        }
                    if progress.status == "running":
                        self.recovery_bridge.transition_task(
                            self.run_id, active.task_id, "verifying",
                            reason="durable worker completed; host acceptance verification started",
                        )
                    self.recovery_bridge.transition_task(
                        self.run_id, active.task_id, "passed",
                        reason="host completion verifier accepted the task evidence",
                        evidence_refs=evidence_refs,
                    )
                    continue
                if durable.state in {"failed", "blocked", "cancelled"}:
                    target = "cancelled" if durable.state == "cancelled" else (
                        "blocked" if durable.state == "blocked" else "failed"
                    )
                    self.recovery_bridge.transition_task(
                        self.run_id, active.task_id, target,
                        reason=f"canonical durable task ended in {durable.state}",
                    )
                    return {
                        "status": "cancelled" if target == "cancelled" else "blocked",
                        "blocker": f"task {active.task_id} ended in {durable.state}",
                        "task_id": active.task_id,
                        "next_action": "review_failure_and_replan_with_new_evidence",
                    }
                return {
                    "status": "waiting_external_dependency",
                    "blocker": f"task {active.task_id} has unknown durable state {durable.state!r}",
                    "next_action": "reconcile_unknown_task_state",
                }

            blocked = [
                item.task_id for item in run.tasks
                if item.status in {"blocked", "failed"}
            ]
            if blocked:
                return {
                    "status": "blocked",
                    "blocker": "plan contains failed or blocked tasks: " + ", ".join(blocked),
                    "next_action": "review_failure_and_replan_with_new_evidence",
                }
            cancelled = [item.task_id for item in run.tasks if item.status == "cancelled"]
            if cancelled:
                return {
                    "status": "cancelled",
                    "blocker": "plan contains cancelled tasks: " + ", ".join(cancelled),
                    "next_action": "operator_review_required",
                }

            ready = run.ready_tasks(plan)
            if ready:
                task_id = ready[0]
                task = next(item for item in plan.tasks if item.task_id == task_id)
                generation = progress_by_id[task_id].replan_generation
                return self._dispatch(
                    plan, run, task,
                    work_key=self.work_key(task, generation),
                )
            return {
                "status": "in_progress",
                "run_id": self.run_id,
                "next_action": "wait_for_dependency_or_new_evidence",
            }

        return {
            "status": "blocked",
            "run_id": self.run_id,
            "blocker": "plan reconciliation exceeded its bounded transition budget",
            "next_action": "inspect_plan_and_recovery_ledger",
        }
