"""Capability-aware, deterministic execution runtime for specialist agents."""
from __future__ import annotations
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Callable, Mapping
from .agency_registry import rank
from .agency_quality import ReviewFinding, QualityReceipt, evaluate
from .agency_provenance import ProvenanceLedger
from .agency_observability import Tracer
from .agency_trace_export import RegressionLink, apply_regression_score, correlate_trace_with_regression
from .agency_regression_loop import PromotionDecision, PromotionPolicy, RegressionPlan, RegressionRun, run_trace_regression_loop
from .agency_release_lifecycle import ArtifactRef, ArtifactStore, ReleaseState, apply_promotion_decision


@dataclass(frozen=True)
class TaskProfile:
    task_id: str
    request: str
    artifact_type: str = "code"
    risk: str = "normal"
    mutation_mode: str = "bounded"
    acceptance: tuple[str, ...] = ()
    evidence_required: tuple[str, ...] = ()
    technologies: tuple[str, ...] = ()
    support_limit: int = 2
    context: object | None = None

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.request.strip():
            raise ValueError("task_id and request are required")
        if self.mutation_mode not in {"read-only", "bounded", "serialized"}:
            raise ValueError("unsupported mutation mode")
        if self.support_limit < 0:
            raise ValueError("support_limit must be non-negative")


@dataclass(frozen=True)
class Assignment:
    specialist: str
    role: str
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceItem:
    source: str
    claim: str
    locator: str = ""
    confidence: str = "stated"


@dataclass(frozen=True)
class LedgerEntry:
    event: str
    detail: str
    evidence_ids: tuple[str, ...] = ()

    @property
    def event_id(self) -> str:
        return sha256(f"{self.event}|{self.detail}|{'|'.join(self.evidence_ids)}".encode()).hexdigest()[:16]


@dataclass
class ExecutionResult:
    task: TaskProfile
    assignments: list[Assignment]
    deliverables: list[str] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    findings: list[ReviewFinding] = field(default_factory=list)
    dimensions: dict[str, int] = field(default_factory=dict)
    hard_gates: dict[str, bool] = field(default_factory=dict)
    ledger: list[LedgerEntry] = field(default_factory=list)
    receipt: QualityReceipt | None = None
    trace_id: str | None = None
    regression_link: RegressionLink | None = None
    regression_run: RegressionRun | None = None
    promotion_decision: PromotionDecision | None = None
    release_state: ReleaseState | None = None

    def as_dict(self) -> dict:
        return {
            "task_id": self.task.task_id,
            "assignments": [a.__dict__ for a in self.assignments],
            "deliverables": self.deliverables,
            "evidence": [e.__dict__ for e in self.evidence],
            "verification": self.verification,
            "findings": [f.__dict__ for f in self.findings],
            "dimensions": self.dimensions,
            "hard_gates": self.hard_gates,
            "ledger": [{"event_id": e.event_id, "event": e.event, "detail": e.detail, "evidence_ids": e.evidence_ids} for e in self.ledger],
            "receipt": self.receipt.as_dict() if self.receipt else None,
            "trace_id": self.trace_id,
            "regression": self.regression_link.as_dict() if self.regression_link else None,
            "regression_run": self.regression_run.as_dict() if self.regression_run else None,
            "promotion": self.promotion_decision.as_dict() if self.promotion_decision else None,
            "release": self.release_state.as_dict() if self.release_state else None,
        }

    def provenance(self) -> ProvenanceLedger:
        ledger = ProvenanceLedger()
        for entry in self.ledger:
            ledger.append(self.task.task_id, entry.event, entry.detail, entry.evidence_ids)
        return ledger


def plan_task(task: TaskProfile, registry: Mapping[str, object] | None = None, support_limit: int | None = None) -> list[Assignment]:
    limit = task.support_limit if support_limit is None else support_limit
    if limit < 0:
        raise ValueError("support_limit must be non-negative")
    ranked = rank(task.request + " " + task.artifact_type + " " + " ".join(task.technologies), registry=registry, limit=max(3, limit + 1))
    if not ranked:
        return [Assignment("generalist-engineer", "primary", 0, ("fallback:no-specialist-registry",))]
    assignments = [Assignment(str(ranked[0]["name"]), "primary", int(ranked[0]["score"]), tuple(ranked[0]["reasons"]))]
    for candidate in ranked[1:limit + 1]:
        role = "reviewer" if str(candidate.get("division", "")).lower() in {"testing", "security"} else "support"
        assignments.append(Assignment(str(candidate["name"]), role, int(candidate["score"]), tuple(candidate["reasons"])))
    return assignments


def evidence_id(item: EvidenceItem) -> str:
    return sha256(f"{item.source}|{item.claim}|{item.locator}".encode()).hexdigest()[:16]


def _receipt_score(receipt: QualityReceipt | None) -> float:
    if receipt is None:
        return 0.0
    score = getattr(receipt, "score", None)
    if isinstance(score, (int, float)):
        return max(0.0, min(1.0, float(score) / 100.0))
    return 1.0 if getattr(receipt, "passed", False) else 0.0


def execute(
    task: TaskProfile,
    worker: Callable[[TaskProfile, list[Assignment]], Mapping[str, object]],
    registry: Mapping[str, object] | None = None,
    rubric: Mapping[str, object] | None = None,
    support_limit: int | None = None,
    tracer: Tracer | None = None,
    regression_id: str | None = None,
    regression_result: object | None = None,
    regression_plan: RegressionPlan | None = None,
    promotion_policy: PromotionPolicy | None = None,
    release_store: ArtifactStore | None = None,
    release_artifact: str | None = None,
    release_artifact_id: str | None = None,
) -> ExecutionResult:
    """Execute a task and optionally close trace -> regression -> release.

    With ``release_store`` and ``release_artifact`` supplied, AUREN materializes
    the decision as a real immutable artifact/channel transition. Shadow and
    canary stage an artifact without replacing ``current``; promote atomically
    advances ``current``; rollback selects the prior promoted immutable build.
    """
    if regression_result is not None and not regression_id:
        raise ValueError("regression_id is required when regression_result is supplied")
    if regression_plan is not None and regression_result is not None:
        raise ValueError("regression_plan and regression_result are mutually exclusive")
    if release_artifact and not release_store:
        raise ValueError("release_store is required when release_artifact is supplied")
    tracer = tracer or Tracer()
    trace = tracer.start("auren.coding_task", {"task_id": task.task_id, "risk": task.risk, "mutation_mode": task.mutation_mode})
    try:
        plan_span = trace.span("planning", "planner", {"request": task.request, "artifact_type": task.artifact_type})
        assignments = plan_task(task, registry=registry, support_limit=support_limit)
        plan_span.finish({"assignments": [a.__dict__ for a in assignments]})

        result = ExecutionResult(task, assignments)
        result.trace_id = trace.trace_id
        result.ledger.append(LedgerEntry("planned", f"selected {len(assignments)} specialist(s)"))

        worker_span = trace.span("agent_execution", "agent", {"task_id": task.task_id, "specialists": [a.specialist for a in assignments]})
        try:
            raw = dict(worker(task, assignments))
            worker_span.finish({"deliverable_count": len(raw.get("deliverables", ())), "verification_count": len(raw.get("verification", ()))})
        except Exception as exc:
            worker_span.finish({"error": type(exc).__name__}, "error")
            raise

        result.deliverables = [str(x) for x in raw.get("deliverables", ())]
        result.verification = [str(x) for x in raw.get("verification", ())]
        result.dimensions = {str(k): int(v) for k, v in dict(raw.get("dimensions", {})).items()}
        result.hard_gates = {str(k): bool(v) for k, v in dict(raw.get("hard_gates", {})).items()}
        result.findings = list(raw.get("findings", ()))
        evidence_span = trace.span("evidence", "retrieval", {"count": len(raw.get("evidence", ()))})
        for item in raw.get("evidence", ()):
            evidence = item if isinstance(item, EvidenceItem) else EvidenceItem(**item)
            result.evidence.append(evidence)
            result.ledger.append(LedgerEntry("evidence", evidence.claim, (evidence_id(evidence),)))
        evidence_span.finish({"evidence_count": len(result.evidence)})

        verify_span = trace.span("verification", "verifier", {"checks": result.verification})
        result.ledger.append(LedgerEntry("verified", "; ".join(result.verification)))
        result.receipt = evaluate(result.dimensions, result.hard_gates, result.findings, [e.claim for e in result.evidence], rubric=rubric)
        score = _receipt_score(result.receipt)
        verify_span.score("quality", score, "normalized quality receipt", "auren")
        verify_span.finish({"passed": bool(getattr(result.receipt, "passed", False)), "quality": score})
        trace.score("task_quality", score, "quality receipt", "auren")

        if regression_plan is not None:
            regression_span = trace.span("regression", "evaluation", {"dataset": regression_plan.dataset.name, "dataset_version": regression_plan.dataset.version})
            regression_run, decision = run_trace_regression_loop(
                trace,
                regression_plan,
                quality=score,
                hard_gates=result.hard_gates,
                policy=promotion_policy or PromotionPolicy(),
                regression_id=regression_id,
            )
            result.regression_run = regression_run
            result.regression_link = correlate_trace_with_regression(trace, regression_run.regression_id, regression_run.result)
            apply_regression_score(trace, result.regression_link)
            result.promotion_decision = decision
            regression_span.score("regression_pass", 1.0 if regression_run.passed else 0.0, "AUREN-owned regression outcome", "regression")
            regression_span.finish({"regression_id": regression_run.regression_id, "passed": regression_run.passed, "dataset_digest": regression_run.result.dataset_digest, "failures": regression_run.result.failures, "promotion": decision.action})
            trace.metadata["promotion"] = decision.as_dict()
            result.ledger.append(LedgerEntry("regression", f"{regression_run.regression_id}: {'passed' if regression_run.passed else 'failed'}"))
            result.ledger.append(LedgerEntry("promotion", f"{decision.action}: {decision.reason}"))

            if release_store is not None:
                artifact_ref: ArtifactRef | None = None
                if decision.action != "rollback":
                    if not release_artifact:
                        raise ValueError("release_artifact is required for shadow/canary/promote decisions")
                    artifact_ref = release_store.stage(release_artifact, release_artifact_id or task.task_id)
                result.release_state = apply_promotion_decision(release_store, decision, artifact_ref)
                trace.metadata["release"] = result.release_state.as_dict()
                trace.score("release_applied", 1.0, "release state transition completed", "auren")
                result.ledger.append(LedgerEntry("release", f"{result.release_state.action}: {result.release_state.artifact_id or 'previous'}"))
        elif regression_result is not None and regression_id:
            regression_span = trace.span("regression", "evaluation", {"regression_id": regression_id})
            result.regression_link = correlate_trace_with_regression(trace, regression_id, regression_result)
            apply_regression_score(trace, result.regression_link)
            regression_span.score("regression_pass", 1.0 if result.regression_link.passed else 0.0, "linked regression outcome", "regression")
            regression_span.finish({"passed": result.regression_link.passed, "dataset_digest": result.regression_link.dataset_digest, "failures": result.regression_link.failures})
            result.ledger.append(LedgerEntry("regression", f"{regression_id}: {'passed' if result.regression_link.passed else 'failed'}"))

        tracer.end(trace)
        return result
    except Exception:
        tracer.end(trace, "error")
        raise
