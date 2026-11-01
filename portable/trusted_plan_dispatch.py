"""Trusted host composition root for plan-to-worker dispatch.

The host resolves live budget capabilities and canonical durable approvals. Callers
supply task intent and context packets only; they cannot manufacture receipt metadata.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .action_governance import ActionRequest
from .durable_approval import DurableApprovalStore
from .engineering_context_packet import EngineeringContextPacket
from .engineering_plan_dispatch import EngineeringPlanDispatch
from .engineering_plan_recovery import EngineeringPlanRecoveryBridge
from .engineering_plan_store import EngineeringPlanStore
from .execution_controller import ExecutionController
from .runtime_recovery import RuntimeExecutionGuard


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _required_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


class TrustedPlanDispatchRoot:
    """Resolve authoritative execution state immediately before durable queue insertion.

    Resolver callbacks are host-bootstrap dependencies. They must read the canonical
    supervisor guard/checkpoint and approval store; they must not trust agent payloads.
    Configuring this root fences the legacy controller dispatch entry point.
    """

    def __init__(
        self,
        *,
        controller: ExecutionController,
        store: EngineeringPlanStore,
        recovery_bridge: EngineeringPlanRecoveryBridge,
        approval_store: DurableApprovalStore,
        budget_resolver: Callable[[str, str, str], Mapping[str, Any]],
        approval_resolver: Callable[[str, str, str], Mapping[str, Any] | None],
    ) -> None:
        if not isinstance(controller, ExecutionController):
            raise TypeError("controller must be the live ExecutionController")
        if not isinstance(store, EngineeringPlanStore):
            raise TypeError("store must be the canonical EngineeringPlanStore")
        if not isinstance(recovery_bridge, EngineeringPlanRecoveryBridge) or recovery_bridge.store is not store:
            raise ValueError("recovery_bridge must validate the same canonical plan store")
        if not isinstance(approval_store, DurableApprovalStore) or approval_store.controller is not controller:
            raise ValueError("approval_store must share the live controller database")
        if not callable(budget_resolver) or not callable(approval_resolver):
            raise ValueError("trusted budget and approval resolvers are required")
        existing = getattr(controller, "_trusted_plan_dispatch_token", None)
        if existing is not None:
            raise RuntimeError("a trusted plan dispatch root is already registered")
        self.controller = controller
        self.store = store
        self.recovery_bridge = recovery_bridge
        self.approval_store = approval_store
        self.budget_resolver = budget_resolver
        self.approval_resolver = approval_resolver
        self._token = object()
        controller._trusted_plan_dispatch_required = True
        controller._trusted_plan_dispatch_token = self._token

    def _budget_binding(
        self, run_id: str, task_id: str, handler: str,
    ) -> tuple[dict[str, str], float | None]:
        resolved = self.budget_resolver(run_id, task_id, handler)
        if not isinstance(resolved, Mapping):
            raise PermissionError("trusted supervisor did not resolve a shared budget")
        budget_id = resolved.get("budget_id")
        guard = resolved.get("guard")
        policy_digest = _required_digest(resolved.get("policy_digest"), "budget policy digest")
        if not isinstance(budget_id, str) or not budget_id.strip():
            raise PermissionError("trusted supervisor returned no budget identity")
        if not isinstance(guard, RuntimeExecutionGuard):
            raise PermissionError("trusted supervisor did not return the canonical RuntimeExecutionGuard")
        guard.check()
        snapshot = guard.snapshot()
        live_checkpoint_digest = _required_digest(snapshot.get("digest"), "live budget checkpoint digest")
        # Validate the live guard before dispatch. The bound digest is the immutable
        # admission checkpoint returned by the supervisor ledger, not a mutable
        # post-charge snapshot; this keeps idempotent replay stable after worker use.
        RuntimeExecutionGuard.resume(snapshot)
        checkpoint_digest = _required_digest(
            resolved.get("checkpoint_digest"), "canonical supervisor admission checkpoint digest",
        )
        if not live_checkpoint_digest:
            raise PermissionError("live supervisor budget checkpoint is invalid")
        remaining_seconds = snapshot.get("remaining_seconds")
        if remaining_seconds is not None:
            if (
                isinstance(remaining_seconds, bool)
                or not isinstance(remaining_seconds, (int, float))
                or remaining_seconds <= 0
            ):
                raise PermissionError("canonical supervisor deadline is exhausted or invalid")
            remaining_seconds = float(remaining_seconds)
        return ({
            "budget_id": budget_id.strip(),
            "checkpoint_digest": checkpoint_digest,
            "policy_digest": policy_digest,
        }, remaining_seconds)

    def _approval_binding(
        self, run_id: str, task_id: str, handler: str, packet: EngineeringContextPacket,
    ) -> dict[str, str] | None:
        resolved = self.approval_resolver(run_id, task_id, handler)
        execution_class = self.controller._handler_execution_class(handler)
        if resolved is None:
            if execution_class == "consequential":
                raise PermissionError("consequential dispatch requires a canonical durable approval")
            return None
        if not isinstance(resolved, Mapping):
            raise PermissionError("trusted approval resolver returned an invalid record reference")
        approval_id = resolved.get("approval_id")
        request = resolved.get("request")
        if not isinstance(approval_id, str) or not approval_id.strip() or not isinstance(request, ActionRequest):
            raise PermissionError("trusted approval resolver must return an approval_id and ActionRequest")
        record = self.approval_store.get(approval_id)
        if record.status != "approved":
            raise PermissionError(f"durable approval is {record.status}, not approved")
        try:
            expires_at = datetime.fromisoformat(record.expires_at)
        except (TypeError, ValueError) as exc:
            raise PermissionError("durable approval expiry is invalid") from exc
        try:
            current_time = self.approval_store.clock()
            if not isinstance(current_time, datetime) or current_time.tzinfo is None:
                raise ValueError("approval store clock must return an aware datetime")
            current_time = current_time.astimezone(timezone.utc)
        except Exception as exc:
            raise PermissionError("durable approval clock could not be verified") from exc
        if expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= current_time:
            raise PermissionError("durable approval is expired")
        if request.task_id != record.task_id or request.fingerprint() != record.request_fingerprint:
            raise PermissionError("durable approval is not bound to the resolved operation")
        if not isinstance(packet, EngineeringContextPacket):
            raise TypeError("approval verification requires the immutable EngineeringContextPacket")
        packet.verify()
        checkpoint = self.approval_store.checkpoint_for(approval_id)
        expected_plan_binding = {
            "plan_run_id": run_id,
            "plan_task_id": task_id,
            "plan_digest": packet.plan_digest,
            "run_revision": packet.run_revision,
            "source_revision": packet.source_revision,
        }
        if any(checkpoint.get(name) != value for name, value in expected_plan_binding.items()):
            raise PermissionError("durable approval checkpoint is not bound to this exact plan task revision")
        if not self.approval_store.verify_audit_chain():
            raise PermissionError("durable approval audit chain failed verification")
        approval_events = self.approval_store.events(approval_id)
        request_events = [item for item in approval_events if item["event_type"] == "approval_requested"]
        if len(request_events) != 1:
            raise PermissionError("durable approval request event is missing or ambiguous")
        request_details = request_events[0]["event"].get("details", {})
        if (
            request_details.get("expires_at") != record.expires_at
            or request_details.get("request_fingerprint") != record.request_fingerprint
            or request_details.get("artifact_digest") != record.artifact_digest
            or request_details.get("evidence_digest") != record.evidence_digest
            or request_details.get("policy_digest") != record.policy_digest
            or request_details.get("scope") != record.scope
            or request_details.get("required_role") != record.required_role
        ):
            raise PermissionError("durable approval row differs from its immutable request event")
        if not approval_events:
            raise PermissionError("durable approval has no audit events")
        final_event = approval_events[-1]["event"]
        if final_event.get("to_state") != record.status or final_event.get("version") != record.version:
            raise PermissionError("durable approval state differs from its append-only audit chain")
        if self.approval_store.context_provider is None:
            raise PermissionError("durable approval has no trusted current-context provider")
        context = self.approval_store.context_provider(request)
        if not isinstance(context, Mapping):
            raise PermissionError("durable approval context could not be recomputed")
        for name in ("artifact_digest", "evidence_digest", "policy_digest"):
            supplied = _required_digest(context.get(name), f"current {name}")
            if supplied != getattr(record, name):
                raise PermissionError(f"durable approval {name} no longer matches current context")
        votes = [
            item for item in self.approval_store.events(approval_id)
            if item["event_type"] == "approval_vote_recorded"
            and item["event"].get("details", {}).get("decision") == "approve"
        ]
        approvers = sorted({str(item["actor"]) for item in votes})
        if len(approvers) < record.required_approvals:
            raise PermissionError("durable approval vote threshold is not met")
        if record.separation_of_duties and record.requester_id in approvers:
            raise PermissionError("requester self-approval is not permitted")
        receipt_digest = _canonical_digest({
            "approval_id": record.approval_id,
            "task_id": record.task_id,
            "operation_id": record.operation_id,
            "request_fingerprint": record.request_fingerprint,
            "artifact_digest": record.artifact_digest,
            "evidence_digest": record.evidence_digest,
            "policy_digest": record.policy_digest,
            "requester_id": record.requester_id,
            "required_role": record.required_role,
            "scope": record.scope,
            "version": record.version,
            "status": record.status,
            "expires_at": record.expires_at,
            "request_event_digest": request_events[0]["event_digest"],
            "decision_event_digests": [item["event_digest"] for item in votes],
        })
        return {
            "approval_id": record.approval_id,
            "receipt_digest": receipt_digest,
            "evidence_digest": record.evidence_digest,
            "policy_digest": record.policy_digest,
            "approver_id": ",".join(approvers),
            "approver_role": record.required_role,
            "decision": "approved",
        }

    def dispatch(
        self,
        *,
        run_id: str,
        task_id: str,
        dispatch_key: str,
        handler: str,
        packet: EngineeringContextPacket,
        payload: Mapping[str, Any] | None = None,
        goal: str | None = None,
        deadline_seconds: float | None = None,
    ):
        if payload is not None and not isinstance(payload, Mapping):
            raise ValueError("dispatch payload must be a mapping")
        safe_payload = dict(payload or {})
        budget, remaining_seconds = self._budget_binding(run_id, task_id, handler)
        if deadline_seconds is not None:
            if (
                isinstance(deadline_seconds, bool)
                or not isinstance(deadline_seconds, (int, float))
                or deadline_seconds <= 0
                or deadline_seconds > 86400
            ):
                raise ValueError("deadline_seconds must be finite, positive, and at most one day")
            deadline_seconds = float(deadline_seconds)
        if remaining_seconds is not None:
            # A caller may shorten the supervisor deadline, never extend it.
            bounded_deadline = min(remaining_seconds, 86400.0)
            deadline_seconds = (
                bounded_deadline if deadline_seconds is None
                else min(deadline_seconds, bounded_deadline)
            )
        approval = self._approval_binding(run_id, task_id, handler, packet)
        return EngineeringPlanDispatch(
            store=self.store, controller=self.controller, recovery_bridge=self.recovery_bridge,
        ).dispatch(
            run_id=run_id,
            task_id=task_id,
            dispatch_key=dispatch_key,
            handler=handler,
            packet=packet,
            payload=safe_payload,
            goal=goal,
            deadline_seconds=deadline_seconds,
            shared_budget_binding=budget,
            approval_evidence=approval,
            _trusted_root_token=self._token,
        )


__all__ = ["TrustedPlanDispatchRoot"]
