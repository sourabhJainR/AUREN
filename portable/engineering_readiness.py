"""Three-axis engineering entry gate and bounded research/POC budgets.

The gate is a deterministic admission contract, not a model-quality claim. Gap
records and phase budgets are append-only events in the canonical ProgressJournal;
evidence lineage uses EngineeringEvidenceEnvelope. Execution authority remains
ExecutionController.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Event
import hashlib
import json
import time
from typing import Any, Callable, Mapping

from .engineering_evidence_envelope import EngineeringEvidenceEnvelope, EvidenceRef
from .progress_journal import ProgressJournal
from .runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard

AXES = ("technology", "intent", "cognitive")
AXIS_OWNERS = {
    "technology": ("Platform Engineering", "ARCH-01"),
    "intent": ("Product and Engineering Management", "EM-ENG-01"),
    "cognitive": ("Research and Knowledge Engineering", "RE-01"),
}
DEFAULT_REMEDIATION = {
    "technology": (
        "Identify the required tools, provider capabilities, permissions and target environment.",
        "Attach current capability and environment evidence; record unavailable dependencies.",
    ),
    "intent": (
        "Clarify the desired outcome, scope, constraints and measurable acceptance criteria.",
        "Record the authorized decision-maker and explicit stopping conditions.",
    ),
    "cognitive": (
        "Resolve missing context, unsupported assumptions and unanswered technical questions.",
        "Attach relevant evidence, prior failure context and an independently checkable verification path.",
    ),
}
PHASE_LIMIT_SECONDS = {
    "research": 45 * 60,
    "poc": 90 * 60,
    "poc_correction": 90 * 60,
    "evaluation": 25 * 60,
}
PHASE_ATTEMPT_LIMIT = {"research": 1, "poc": 1, "poc_correction": 1, "evaluation": 1}
BASIC_CYCLE_LIMIT_SECONDS = 190 * 60


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any, field: str, *, limit: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{field} must be non-empty text of at most {limit} characters")
    return value.strip()


def department_for_role(role_id: str | None, fallback: str = "Engineering") -> str:
    """Map existing organization role prefixes to departments without inventing people."""
    prefix = (role_id or "").split("-", 1)[0].upper()
    return {
        "BE": "Backend Engineering", "FE": "Frontend Engineering",
        "QA": "Quality Engineering", "SEC": "Security",
        "SRE": "Reliability and Operations", "ARCH": "Architecture",
        "RE": "Research and Evaluation", "DATA": "Data and Knowledge",
        "PM": "Product", "EM": "Engineering Management",
        "CTO": "Architecture and Technology Leadership",
    }.get(prefix, fallback)


def infer_engineering_phase(*values: str) -> str:
    text = " ".join(value.lower() for value in values if isinstance(value, str))
    if any(token in text for token in ("poc", "proof of concept", "prototype", "experiment", "spike")):
        return "poc"
    if any(token in text for token in ("research", "investigate", "literature review", "discovery")):
        return "research"
    if any(token in text for token in ("evaluate", "benchmark", "measure")):
        return "evaluation"
    return "implementation"


@dataclass(frozen=True)
class ReadinessAxis:
    axis: str
    status: str
    summary: str
    evidence_refs: tuple[str, ...]
    department: str
    owner_role_id: str
    remediation: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.axis not in AXES:
            raise ValueError(f"unknown readiness axis: {self.axis}")
        if self.status not in {"ready", "gap", "not_applicable"}:
            raise ValueError(f"invalid readiness status for {self.axis}: {self.status}")
        _text(self.summary, f"{self.axis}.summary")
        for name, values in (("evidence_refs", self.evidence_refs),
                             ("remediation", self.remediation),
                             ("acceptance_criteria", self.acceptance_criteria)):
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"{self.axis}.{name} contains an empty value")
        if self.status == "ready" and not self.evidence_refs:
            raise ValueError(f"ready {self.axis} assessment requires evidence references")
        if self.status == "gap" and (not self.remediation or not self.acceptance_criteria):
            raise ValueError(f"gap {self.axis} assessment requires remediation and acceptance criteria")
        if self.status == "not_applicable" and (not self.acceptance_criteria or not self.evidence_refs):
            raise ValueError(f"not_applicable {self.axis} assessment requires a reason and evidence references")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_refs"] = list(self.evidence_refs)
        value["remediation"] = list(self.remediation)
        value["acceptance_criteria"] = list(self.acceptance_criteria)
        return value


@dataclass(frozen=True)
class EngineeringReadinessAssessment:
    task_id: str
    work_key: str
    intent_digest: str
    repository_snapshot_digest: str
    axes: tuple[ReadinessAxis, ...]
    envelope: EngineeringEvidenceEnvelope
    assessment_digest: str
    gap_ids: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return all(axis.status in {"ready", "not_applicable"} for axis in self.axes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "auren-engineering-readiness-v1",
            "task_id": self.task_id,
            "work_key": self.work_key,
            "intent_digest": self.intent_digest,
            "repository_snapshot_digest": self.repository_snapshot_digest,
            "status": "ready" if self.ready else "blocked",
            "axes": {axis.axis: axis.to_dict() for axis in self.axes},
            "envelope": self.envelope.as_dict(),
            "envelope_digest": self.envelope.envelope_digest,
            "assessment_digest": self.assessment_digest,
            "gap_ids": list(self.gap_ids),
        }


class EngineeringReadinessBlocked(RuntimeError):
    """An engineering task has one or more unresolved entry-gate gaps."""

    def __init__(self, assessment: EngineeringReadinessAssessment):
        self.assessment = assessment
        self.gap_ids = assessment.gap_ids
        super().__init__(
            "engineering entry gate blocked: " +
            ", ".join(f"{axis.axis}={axis.status}" for axis in assessment.axes if axis.status == "gap")
        )


class EngineeringReadinessGate:
    """Assess TECH/INTENT/COGNITIVE and persist gaps in the canonical journal."""

    def __init__(self, journal: ProgressJournal):
        if not isinstance(journal, ProgressJournal):
            raise TypeError("EngineeringReadinessGate requires the canonical ProgressJournal")
        self.journal = journal

    @staticmethod
    def _axis(axis: str, raw: Any) -> ReadinessAxis:
        department, owner = AXIS_OWNERS[axis]
        if not isinstance(raw, Mapping):
            return ReadinessAxis(
                axis, "gap", f"{axis} readiness has not been assessed.", (),
                department, owner, DEFAULT_REMEDIATION[axis],
                (f"Provide current {axis} evidence and verify the remediation.",),
            )
        status = raw.get("status", "gap")
        summary = raw.get("summary", f"{axis} readiness is not established.")
        refs = raw.get("evidence_refs", ())
        remediation = raw.get("remediation", DEFAULT_REMEDIATION[axis])
        acceptance = raw.get("acceptance_criteria", (f"Verify {axis} readiness with current evidence.",))
        if isinstance(refs, str) or not isinstance(refs, (list, tuple)):
            raise ValueError(f"{axis}.evidence_refs must be an array")
        if isinstance(remediation, str) or not isinstance(remediation, (list, tuple)):
            raise ValueError(f"{axis}.remediation must be an array")
        if isinstance(acceptance, str) or not isinstance(acceptance, (list, tuple)):
            raise ValueError(f"{axis}.acceptance_criteria must be an array")
        return ReadinessAxis(
            axis=axis, status=str(status), summary=_text(summary, f"{axis}.summary"),
            evidence_refs=tuple(_text(item, f"{axis}.evidence_ref", limit=512) for item in refs),
            department=_text(raw.get("department", department), f"{axis}.department", limit=160),
            owner_role_id=_text(raw.get("owner_role_id", owner), f"{axis}.owner_role_id", limit=100),
            remediation=tuple(_text(item, f"{axis}.remediation", limit=1000) for item in remediation),
            acceptance_criteria=tuple(_text(item, f"{axis}.acceptance_criteria", limit=1000) for item in acceptance),
        )

    def _build_assessment(
        self, *, task_id: str, work_key: str, intent_digest: str,
        repository_snapshot_digest: str, axes: Mapping[str, Any],
        context_plan_digest: str = "",
    ) -> EngineeringReadinessAssessment:
        """Build a deterministic assessment without mutating the journal."""
        task_id = _text(task_id, "task_id", limit=256)
        work_key = _text(work_key, "work_key", limit=256)
        intent_digest = _text(intent_digest, "intent_digest", limit=128)
        snapshot = _text(repository_snapshot_digest, "repository_snapshot_digest", limit=256)
        if not isinstance(axes, Mapping):
            raise TypeError("axes must be a mapping")
        unknown_axes = set(axes) - set(AXES)
        if unknown_axes:
            raise ValueError(f"unknown readiness axes: {sorted(str(item) for item in unknown_axes)}")
        parsed = tuple(self._axis(axis, axes.get(axis)) for axis in AXES)
        assessment_payload = {
            "schema_version": "auren-engineering-readiness-v1",
            "task_id": task_id, "work_key": work_key,
            "intent_digest": intent_digest, "repository_snapshot_digest": snapshot,
            "axes": [axis.to_dict() for axis in parsed],
        }
        assessment_digest = _digest(assessment_payload)
        gap_ids = tuple(
            _digest({"work_key": work_key, "intent_digest": intent_digest, "axis": axis.axis})[:24]
            for axis in parsed if axis.status == "gap"
        )
        refs: list[EvidenceRef] = []
        gap_index = 0
        for axis in parsed:
            if axis.evidence_refs:
                refs.extend(EvidenceRef(ref, snapshot, "current") for ref in axis.evidence_refs)
            elif axis.status == "gap":
                gap_id = gap_ids[gap_index]
                gap_index += 1
                refs.append(EvidenceRef(f"engineering-gap:{gap_id}", snapshot, "durable-journal"))
        unique_refs = tuple({ref.evidence_id: ref for ref in refs}.values())
        envelope = EngineeringEvidenceEnvelope(
            task_id=task_id, intent_digest=intent_digest,
            repository_snapshot_digest=snapshot, evidence=unique_refs,
            context_plan_digest=context_plan_digest,
            metadata=tuple(sorted({
                "assessment_digest": assessment_digest,
                "readiness_status": "ready" if not gap_ids else "blocked",
                "work_key_digest": _digest(work_key),
            }.items())),
        )
        return EngineeringReadinessAssessment(
            task_id, work_key, intent_digest, snapshot, parsed, envelope,
            assessment_digest, gap_ids,
        )

    def assess(
        self, *, task_id: str, work_key: str, intent_digest: str,
        repository_snapshot_digest: str, axes: Mapping[str, Any],
        context_plan_digest: str = "",
    ) -> EngineeringReadinessAssessment:
        assessment = self._build_assessment(
            task_id=task_id, work_key=work_key, intent_digest=intent_digest,
            repository_snapshot_digest=repository_snapshot_digest, axes=axes,
            context_plan_digest=context_plan_digest,
        )
        self._persist(assessment)
        return assessment

    def assess_packet(
        self, *, work_key: str, plan: Any, task: Any, packet: Any,
        explicit_axes: Mapping[str, Any] | None = None,
    ) -> EngineeringReadinessAssessment:
        """Build the default structural checks from the canonical immutable context packet."""
        plan_digest = _text(getattr(plan, "digest", ""), "plan_digest", limit=128)
        task_id = _text(getattr(task, "task_id", ""), "plan_task_id", limit=256)
        packet_digest = _text(getattr(packet, "packet_digest", ""), "packet_digest", limit=128)
        source_revision = _text(getattr(packet, "source_revision", ""), "source_revision", limit=256)
        requirements = tuple(getattr(plan, "requirements", ()) or ())
        acceptance = tuple(getattr(task, "acceptance_criteria", ()) or ())
        rollback = _text(getattr(task, "rollback_plan", ""), "rollback_plan", limit=2000)
        next_action = _text(getattr(packet, "next_safe_action", ""), "next_safe_action", limit=1000)
        policy = tuple(getattr(packet, "policy_constraints", ()) or ())
        questions = tuple(getattr(packet, "unresolved_questions", ()) or ())
        intent = _digest({
            "plan_digest": plan_digest, "task_id": task_id,
            "packet_digest": packet_digest, "source_revision": source_revision,
        })
        snapshot = _digest({"source_revision": source_revision})
        packet_ref = f"context-packet:{packet_digest}"
        axes: Mapping[str, Any]
        if explicit_axes is not None:
            axes = explicit_axes
        else:
            axes = {
                "technology": {
                    "status": "ready" if source_revision and packet_digest and policy else "gap",
                    "summary": "Source revision, immutable context packet and execution constraints are bound. Provider and host capabilities remain subject to dispatch Guard checks.",
                    "evidence_refs": [f"source-revision:{source_revision}", packet_ref],
                    "department": "Platform Engineering", "owner_role_id": "ARCH-01",
                },
                "intent": {
                    "status": "ready" if requirements and acceptance and rollback and next_action else "gap",
                    "summary": "Plan requirements, task acceptance, rollback and next safe action are explicit.",
                    "evidence_refs": [f"engineering-plan:{plan_digest}", packet_ref],
                    "department": "Product and Engineering Management", "owner_role_id": "EM-ENG-01",
                },
                "cognitive": {
                    "status": "gap" if questions else "ready",
                    "summary": (
                        "Unresolved questions remain and must be answered before implementation."
                        if questions else
                        "Context packet contains the task, constraints, rollback, prior-failure field and next safe action with no declared unresolved questions. This is structural context readiness, not proof of reasoning correctness."
                    ),
                    "evidence_refs": [packet_ref],
                    "department": "Research and Knowledge Engineering", "owner_role_id": "RE-01",
                    "remediation": [
                        f"Resolve: {question}" for question in questions
                    ] or list(DEFAULT_REMEDIATION["cognitive"]),
                    "acceptance_criteria": ["No blocking cognitive/context questions remain; evidence and verification path are recorded."],
                },
            }
        return self.assess(
            task_id=task_id, work_key=work_key, intent_digest=intent,
            repository_snapshot_digest=snapshot, axes=axes,
            context_plan_digest=plan_digest,
        )

    def verify_payload(self, payload: Mapping[str, Any]) -> EngineeringReadinessAssessment:
        """Verify a supervisor-owned receipt before allowing any journal mutation."""
        if not isinstance(payload, Mapping):
            raise TypeError("engineering task payload must be a mapping")
        raw = payload.get("_auren_engineering_readiness")
        if not isinstance(raw, Mapping) or raw.get("schema_version") != "auren-engineering-readiness-v1":
            raise ValueError("engineering task is missing a supported durable readiness receipt")
        axes = raw.get("axes")
        if not isinstance(axes, Mapping):
            raise ValueError("engineering readiness receipt has no axis assessments")
        work_key = _text(raw.get("work_key"), "readiness.work_key", limit=256)
        if payload.get("work_key") != work_key:
            raise ValueError("engineering readiness work_key does not match durable task identity")
        assessment = self._build_assessment(
            task_id=str(raw.get("task_id", "")),
            work_key=work_key,
            intent_digest=str(raw.get("intent_digest", "")),
            repository_snapshot_digest=str(raw.get("repository_snapshot_digest", "")),
            axes=axes,
            context_plan_digest=str(raw.get("envelope", {}).get("context_plan_digest", "")) if isinstance(raw.get("envelope"), Mapping) else "",
        )
        if raw.get("assessment_digest") != assessment.assessment_digest:
            raise ValueError("engineering readiness assessment digest mismatch")
        if payload.get("_auren_engineering_readiness_digest") != assessment.assessment_digest:
            raise ValueError("engineering readiness task binding digest mismatch")
        raw_envelope = raw.get("envelope")
        if not isinstance(raw_envelope, Mapping):
            raise ValueError("engineering readiness receipt has no evidence envelope")
        verified_envelope = EngineeringEvidenceEnvelope.from_state(raw_envelope)
        if (
            raw.get("envelope_digest") != assessment.envelope.envelope_digest
            or verified_envelope.envelope_digest != assessment.envelope.envelope_digest
        ):
            raise ValueError("engineering readiness evidence envelope digest mismatch")
        self._persist(assessment)
        return assessment

    def _persist(self, assessment: EngineeringReadinessAssessment) -> None:
        events = self.journal._events()
        existing = [
            row for row in events
            if row.get("task_id") == assessment.work_key
            and row.get("event") == "engineering_readiness_assessed"
            and isinstance(row.get("metadata"), Mapping)
            and row["metadata"].get("assessment_digest") == assessment.assessment_digest
        ]
        if existing:
            return
        status = "running" if assessment.ready else "blocked"
        metadata = assessment.to_dict()
        self.journal.record(
            assessment.work_key, "engineering_readiness_assessed",
            state=status, stage="engineering-readiness",
            message="Three-axis engineering readiness assessed.",
            evidence_ref=assessment.envelope.envelope_digest,
            metadata=metadata,
        )
        prior_gaps: dict[str, dict[str, Any]] = {}
        for row in events:
            metadata = row.get("metadata")
            if row.get("task_id") == assessment.work_key and isinstance(metadata, Mapping):
                if row.get("event") in {"engineering_gap_recorded", "engineering_gap_transition"}:
                    gap_id = metadata.get("gap_id")
                    if isinstance(gap_id, str) and gap_id:
                        prior_gaps[gap_id] = dict(metadata)
        current_axes = {axis.axis: axis for axis in assessment.axes}
        for gap_id, prior in prior_gaps.items():
            axis = current_axes.get(str(prior.get("axis", "")))
            if axis is None or axis.status not in {"ready", "not_applicable"} or not axis.evidence_refs or prior.get("status") == "resolved":
                continue
            resolved = dict(prior)
            resolved.update({
                "status": "resolved",
                "resolution_evidence_refs": list(axis.evidence_refs),
                "transition_note": "Resolved by a newer three-axis assessment with current evidence.",
                "resolution_assessment_digest": assessment.assessment_digest,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            self.journal.record(
                assessment.work_key, "engineering_gap_transition",
                state="running", stage="engineering-gap",
                message=f"Gap {gap_id} resolved by current {axis.axis} evidence.",
                evidence_ref=assessment.envelope.envelope_digest, metadata=resolved,
            )
        gap_by_axis = {axis.axis: axis for axis in assessment.axes if axis.status == "gap"}
        for gap_id, axis in zip(assessment.gap_ids, gap_by_axis.values()):
            record = {
                "schema_version": "auren-engineering-gap-v1",
                "gap_id": gap_id, "task_id": assessment.task_id,
                "work_key": assessment.work_key, "axis": axis.axis,
                "status": "open", "severity": "high",
                "department": axis.department, "owner_role_id": axis.owner_role_id,
                "summary": axis.summary, "evidence_refs": list(axis.evidence_refs),
                "remediation": list(axis.remediation),
                "acceptance_criteria": list(axis.acceptance_criteria),
                "assessment_digest": assessment.assessment_digest,
                "envelope_digest": assessment.envelope.envelope_digest,
            }
            if any(
                row.get("event") == "engineering_gap_recorded"
                and isinstance(row.get("metadata"), Mapping)
                and row["metadata"].get("gap_id") == gap_id
                for row in events
            ):
                continue
            self.journal.record(
                assessment.work_key, "engineering_gap_recorded",
                state="blocked", stage="engineering-gap",
                message=f"Open {axis.axis} readiness gap assigned to {axis.department}.",
                evidence_ref=assessment.envelope.envelope_digest,
                metadata=record,
            )

    def transition_gap(
        self, *, work_key: str, gap_id: str, status: str,
        evidence_refs: tuple[str, ...] = (), note: str = "",
    ) -> None:
        if status not in {"open", "in_progress", "blocked", "resolved"}:
            raise ValueError("gap status must be open, in_progress, blocked or resolved")
        if status == "resolved" and not evidence_refs:
            raise ValueError("resolving a gap requires evidence references")
        current = [
            row for row in self.journal._events()
            if row.get("task_id") == work_key
            and isinstance(row.get("metadata"), Mapping)
            and row["metadata"].get("gap_id") == gap_id
        ]
        if not current:
            raise KeyError(f"unknown engineering gap {gap_id}")
        prior = current[-1]["metadata"]
        if prior.get("status") == "resolved" and status != "resolved":
            raise ValueError("resolved engineering gaps are immutable; create a new gap for regression")
        metadata = dict(prior)
        metadata.update({
            "status": status, "resolution_evidence_refs": list(evidence_refs),
            "transition_note": note[:1000], "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        self.journal.record(
            work_key, "engineering_gap_transition",
            state="running" if status == "resolved" else "blocked",
            stage="engineering-gap", message=f"Gap {gap_id} transitioned to {status}.",
            evidence_ref=prior.get("envelope_digest"), metadata=metadata,
        )


@dataclass(frozen=True)
class EngineeringBudgetReceipt:
    work_key: str
    phase: str
    phase_deadline_epoch: float | None
    cycle_deadline_epoch: float
    remaining_seconds: float


class EngineeringBudgetLedger:
    """Persist absolute phase deadlines and bounded phase attempts in ProgressJournal."""

    def __init__(self, journal: ProgressJournal, *, clock: Callable[[], float] = time.time):
        if not isinstance(journal, ProgressJournal):
            raise TypeError("EngineeringBudgetLedger requires the canonical ProgressJournal")
        self.journal = journal
        self.clock = clock

    def _rows(self, work_key: str) -> list[dict[str, Any]]:
        return [
            row for row in self.journal._events()
            if row.get("task_id") == work_key and row.get("stage") == "engineering-budget"
            and isinstance(row.get("metadata"), Mapping)
        ]

    def begin(
        self, work_key: str, phase: str, *,
        cancellation_event: Event | None = None,
    ) -> EngineeringBudgetReceipt:
        work_key = _text(work_key, "work_key", limit=256)
        if phase not in {"research", "poc", "poc_correction", "evaluation", "implementation", "verification", "review"}:
            raise ValueError(f"unsupported engineering phase: {phase}")
        now = float(self.clock())
        rows = self._rows(work_key)
        cycle = next((row["metadata"] for row in rows if row.get("event") == "engineering_cycle_started"), None)
        if cycle is None:
            cycle = {
                "budget_schema": "auren-engineering-budget-v1",
                "kind": "cycle", "work_key": work_key,
                "started_at_epoch": now,
                "deadline_epoch": now + BASIC_CYCLE_LIMIT_SECONDS,
                "budget_seconds": BASIC_CYCLE_LIMIT_SECONDS,
            }
            self.journal.record(
                work_key, "engineering_cycle_started", state="running",
                stage="engineering-budget", message="Basic engineering cycle budget started.",
                metadata=cycle,
            )
            rows = self._rows(work_key)
        cycle_deadline = float(cycle.get("deadline_epoch", 0))
        if cycle_deadline <= now:
            self._exhausted(work_key, phase, "basic engineering cycle timebox expired")
            raise ExecutionBudgetExceeded("basic engineering cycle timebox expired")

        phase_limit = PHASE_LIMIT_SECONDS.get(phase)
        phase_rows = [row for row in rows if row.get("metadata", {}).get("kind") == "phase"
                      and row["metadata"].get("phase") == phase]
        started = next((row["metadata"] for row in phase_rows if row.get("event") == "engineering_phase_started"), None)
        if phase_limit is not None:
            if started is None:
                attempts = sum(1 for row in phase_rows if row.get("event") == "engineering_phase_started")
                if attempts >= PHASE_ATTEMPT_LIMIT[phase]:
                    raise ExecutionBudgetExceeded(f"{phase} attempt budget exhausted ({PHASE_ATTEMPT_LIMIT[phase]})")
                started = {
                    "budget_schema": "auren-engineering-budget-v1",
                    "kind": "phase", "work_key": work_key, "phase": phase,
                    "started_at_epoch": now, "deadline_epoch": now + phase_limit,
                    "budget_seconds": phase_limit, "attempt": attempts + 1,
                }
                self.journal.record(
                    work_key, "engineering_phase_started", state="running",
                    stage="engineering-budget", message=f"{phase} timebox started.",
                    metadata=started,
                )
            if any(row.get("event") == "engineering_phase_completed" for row in phase_rows):
                raise ExecutionBudgetExceeded(f"{phase} phase is already completed and cannot be replayed")
            phase_deadline = float(started.get("deadline_epoch", 0))
            if phase_deadline <= now:
                self._exhausted(work_key, phase, f"{phase} timebox expired")
                raise ExecutionBudgetExceeded(f"{phase} timebox expired")
        else:
            phase_deadline = None
        deadline = min(cycle_deadline, phase_deadline) if phase_deadline is not None else cycle_deadline
        remaining = deadline - now
        guard = RuntimeExecutionGuard(deadline_seconds=remaining, cancellation_event=cancellation_event)
        guard.check()
        return EngineeringBudgetReceipt(work_key, phase, phase_deadline, cycle_deadline, remaining)

    def complete(self, receipt: EngineeringBudgetReceipt, *, outcome: str, evidence_refs: tuple[str, ...] = ()) -> None:
        if outcome not in {"passed", "failed", "blocked", "cancelled"}:
            raise ValueError("engineering phase outcome is invalid")
        if receipt.phase not in PHASE_LIMIT_SECONDS:
            return
        rows = self._rows(receipt.work_key)
        if any(row.get("event") == "engineering_phase_completed"
               and row.get("metadata", {}).get("phase") == receipt.phase for row in rows):
            return
        metadata = {
            "budget_schema": "auren-engineering-budget-v1", "kind": "phase",
            "work_key": receipt.work_key, "phase": receipt.phase,
            "outcome": outcome, "completed_at_epoch": float(self.clock()),
            "evidence_refs": list(evidence_refs),
        }
        self.journal.record(
            receipt.work_key, "engineering_phase_completed", state="running",
            stage="engineering-budget", message=f"{receipt.phase} phase outcome: {outcome}.",
            metadata=metadata,
        )

    def _exhausted(self, work_key: str, phase: str, reason: str) -> None:
        self.journal.record(
            work_key, "engineering_budget_exhausted", state="blocked",
            stage="engineering-budget", message=reason,
            metadata={
                "budget_schema": "auren-engineering-budget-v1",
                "kind": "exhausted", "work_key": work_key,
                "phase": phase, "reason": reason, "observed_at_epoch": float(self.clock()),
            },
        )


__all__ = [
    "AXES", "AXIS_OWNERS", "BASIC_CYCLE_LIMIT_SECONDS",
    "EngineeringBudgetLedger", "EngineeringBudgetReceipt",
    "EngineeringReadinessAssessment", "EngineeringReadinessBlocked",
    "EngineeringReadinessGate", "PHASE_LIMIT_SECONDS", "ReadinessAxis",
    "department_for_role", "infer_engineering_phase",
]
