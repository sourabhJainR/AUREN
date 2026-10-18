"""Bounded, provenance-preserving context packets for fresh specialist workers.

Packets contain references and explicit task contracts, not ambient credentials or
unbounded chat history. The builder never truncates safety-critical fields to fit.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from .engineering_plan import EngineeringPlan, EngineeringPlanRun, PlanTask


CONTEXT_PACKET_SCHEMA = "auren-engineering-context-packet-v1"


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("context packet fields must be finite JSON-compatible data") from exc


def _text_tuple(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be a sequence of strings, not a string")
    result = tuple(values)
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise ValueError(f"{field_name} entries must be non-empty strings")
    return tuple(item.strip() for item in result)


def _safe_map(values: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    result = dict(values)
    forbidden = ("secret", "password", "api_key", "credential", "private_key")
    for key in result:
        normalized = str(key).lower().replace("-", "_")
        if normalized == "token" or normalized.endswith("_token") or any(part in normalized for part in forbidden):
            raise ValueError(f"{field_name} must not contain credential-like keys")
    _canonical(result)
    return result


@dataclass(frozen=True)
class EngineeringContextPacket:
    packet_schema: str
    packet_digest: str
    run_id: str
    plan_digest: str
    run_revision: int
    source_revision: str
    task: Mapping[str, Any]
    task_status: str
    dependency_status: Mapping[str, str]
    requirements: tuple[str, ...]
    policy_constraints: tuple[str, ...]
    approvals: tuple[str, ...]
    rollback_plan: str
    acceptance_criteria: tuple[str, ...]
    verified_evidence_refs: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    prior_failure_summaries: tuple[str, ...]
    conventions: tuple[str, ...]
    codebase_map_refs: tuple[str, ...]
    budget_remaining: Mapping[str, int]
    next_safe_action: str
    optional_context: tuple[str, ...] = ()

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "packet_schema": self.packet_schema, "run_id": self.run_id,
            "plan_digest": self.plan_digest, "run_revision": self.run_revision,
            "source_revision": self.source_revision, "task": dict(self.task),
            "task_status": self.task_status, "dependency_status": dict(self.dependency_status),
            "requirements": list(self.requirements), "policy_constraints": list(self.policy_constraints),
            "approvals": list(self.approvals), "rollback_plan": self.rollback_plan,
            "acceptance_criteria": list(self.acceptance_criteria),
            "verified_evidence_refs": list(self.verified_evidence_refs),
            "unresolved_questions": list(self.unresolved_questions),
            "prior_failure_summaries": list(self.prior_failure_summaries),
            "conventions": list(self.conventions), "codebase_map_refs": list(self.codebase_map_refs),
            "budget_remaining": dict(self.budget_remaining), "next_safe_action": self.next_safe_action,
            "optional_context": list(self.optional_context),
        }
        if include_digest:
            payload["packet_digest"] = self.packet_digest
        return payload

    @property
    def serialized_size(self) -> int:
        return len(_canonical(self.to_dict()))

    def verify(self) -> None:
        if self.packet_schema != CONTEXT_PACKET_SCHEMA:
            raise ValueError("unsupported context packet schema")
        actual = hashlib.sha256(_canonical(self.to_dict(include_digest=False))).hexdigest()
        if actual != self.packet_digest:
            raise ValueError("context packet digest mismatch")
        if not self.policy_constraints or not self.rollback_plan or not self.next_safe_action:
            raise ValueError("context packet is missing a safety-critical field")
        if not self.acceptance_criteria:
            raise ValueError("context packet has no acceptance criteria")


def build_context_packet(
    *,
    plan: EngineeringPlan,
    run: EngineeringPlanRun,
    task_id: str,
    policy_constraints: Sequence[str],
    approvals: Sequence[str] = (),
    budget_remaining: Mapping[str, int],
    next_safe_action: str,
    unresolved_questions: Sequence[str] = (),
    prior_failure_summaries: Sequence[str] = (),
    conventions: Sequence[str] = (),
    codebase_map_refs: Sequence[str] = (),
    optional_context: Sequence[str] = (),
    max_bytes: int = 16_384,
) -> EngineeringContextPacket:
    """Build a deterministic task packet; optional context is pruned before any contract data."""
    if type(max_bytes) is not int or max_bytes < 512:
        raise ValueError("max_bytes must be an integer >= 512")
    run.verify_against(plan)
    task = next((item for item in plan.tasks if item.task_id == task_id), None)
    if task is None:
        raise KeyError(f"task {task_id!r} is not present in the plan")
    progress = {item.task_id: item for item in run.tasks}
    task_progress = progress[task_id]
    if task_progress.status in {"passed", "cancelled"}:
        raise ValueError(f"cannot dispatch terminal task {task_id!r}")
    dependency_status = {dep: progress[dep].status for dep in task.depends_on}
    if task_progress.status in {"ready", "running", "verifying"}:
        incomplete = [dep for dep, status in dependency_status.items() if status != "passed"]
        if incomplete:
            raise ValueError(f"task {task_id} has incomplete dependencies: {incomplete}")
    constraints = _text_tuple(policy_constraints, "policy_constraints")
    if not constraints:
        raise ValueError("policy_constraints must not be empty")
    action = next_safe_action.strip() if isinstance(next_safe_action, str) else ""
    if not action:
        raise ValueError("next_safe_action must be non-empty")
    budget = _safe_map(budget_remaining, "budget_remaining")
    if any(not isinstance(key, str) or type(value) is not int or value < 0 for key, value in budget.items()):
        raise ValueError("budget_remaining requires named non-negative integer values")
    for name in ("attempts", "steps", "tool_calls", "tokens"):
        if name in budget and budget[name] < 0:
            raise ValueError(f"budget {name} cannot be negative")

    fixed = dict(
        packet_schema=CONTEXT_PACKET_SCHEMA, packet_digest=("0" * 64),
        run_id=plan.run_id, plan_digest=plan.digest, run_revision=run.revision,
        source_revision=plan.source_revision, task=task.to_dict(),
        task_status=task_progress.status, dependency_status=dependency_status,
        requirements=_text_tuple(plan.requirements, "requirements"),
        policy_constraints=constraints, approvals=_text_tuple(approvals, "approvals"),
        rollback_plan=task.rollback_plan,
        acceptance_criteria=task.acceptance_criteria,
        verified_evidence_refs=task_progress.evidence_refs,
        unresolved_questions=_text_tuple(unresolved_questions, "unresolved_questions"),
        prior_failure_summaries=_text_tuple(prior_failure_summaries, "prior_failure_summaries"),
        conventions=_text_tuple(conventions, "conventions"),
        codebase_map_refs=_text_tuple(codebase_map_refs, "codebase_map_refs"),
        budget_remaining=budget, next_safe_action=action,
        optional_context=_text_tuple(optional_context, "optional_context"),
    )
    # Only optional context is trimmed. Every removal is deterministic and preserves the
    # contract, evidence gaps, approvals, safety constraints and rollback instructions.
    packet = EngineeringContextPacket(**fixed)
    while packet.serialized_size > max_bytes and packet.optional_context:
        fixed["optional_context"] = packet.optional_context[:-1]
        packet = EngineeringContextPacket(**fixed)
    if packet.serialized_size > max_bytes:
        raise ValueError(
            f"mandatory context packet exceeds max_bytes ({packet.serialized_size} > {max_bytes}); "
            "reduce the task scope instead of truncating policy or evidence"
        )
    digest = hashlib.sha256(_canonical(packet.to_dict(include_digest=False))).hexdigest()
    packet = EngineeringContextPacket(**{**fixed, "packet_digest": digest})
    packet.verify()
    return packet
