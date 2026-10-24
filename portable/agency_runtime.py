"""Capability-aware, deterministic execution runtime for specialist agents."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from typing import Callable, Mapping
from .agency_registry import rank
from .agency_quality import ReviewFinding, QualityReceipt, evaluate
from .agency_provenance import ProvenanceLedger
from .agency_observability import Tracer
from .agency_trace_export import RegressionLink, apply_regression_score, correlate_trace_with_regression
from .agency_regression_loop import PromotionDecision, PromotionPolicy, RegressionPlan, RegressionRun, run_trace_regression_loop
from .agency_release_lifecycle import ArtifactRef, ArtifactStore, ReleaseState, apply_promotion_decision
from .engineering_personas import EngineeringPlan, PersonaReview, PersonaWorkflow, ReviewRejectedError, canonical_plan_digest, require_approved_review


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
    approved_plan: Mapping[str, object] | None = None
    source_revision: str = ""
    config_digest: str = ""
    session_id: str | None = None
    run_id: str | None = None

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


def _record_worker_observations(trace, worker_span, raw: Mapping[str, object], source_revision: str | None) -> None:
    """Record bounded adapter telemetry without treating it as trusted evaluation evidence."""
    route = raw.get("provider_route")
    if isinstance(route, Mapping):
        provider = route.get("provider")
        model = route.get("model")
        if isinstance(provider, str) and provider.strip():
            worker_span.provider = provider[:120]
        if isinstance(model, str) and model.strip():
            worker_span.model = model[:160]
        worker_span.metadata["provider_route_source"] = "worker_reported"
        worker_span.metadata["route_reason"] = route.get("reason", "")
    else:
        provider, model = raw.get("provider"), raw.get("model")
        if isinstance(provider, str) and provider.strip():
            worker_span.provider = provider[:120]
        if isinstance(model, str) and model.strip():
            worker_span.model = model[:160]
    usage = raw.get("usage")
    if isinstance(usage, Mapping):
        worker_span.metadata["usage"] = {
            str(key): value for key, value in usage.items()
            if str(key) in {"input_tokens", "output_tokens", "total_tokens", "cost_usd", "latency_ms"}
            and isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= float(value) < float("inf")
        }

    events = raw.get("observability_events", ())
    if not isinstance(events, (list, tuple)):
        trace.metadata["worker_observation_errors"] = int(trace.metadata.get("worker_observation_errors", 0)) + 1
        return
    allowed_statuses = {"ok", "error", "blocked", "failed", "cancelled", "timed_out", "unknown", "unscored", "stale"}
    allowed_phases = {"ideation", "research", "planning", "context", "routing", "tool", "implementation", "verification", "learning", "release", "rollback"}
    for event in events[:100]:
        if not isinstance(event, Mapping):
            trace.metadata["worker_observation_errors"] = int(trace.metadata.get("worker_observation_errors", 0)) + 1
            continue
        name = event.get("name")
        phase = event.get("phase")
        if not isinstance(name, str) or not name.strip() or not isinstance(phase, str):
            trace.metadata["worker_observation_errors"] = int(trace.metadata.get("worker_observation_errors", 0)) + 1
            continue
        normalized_phase = phase.lower()
        if normalized_phase not in allowed_phases:
            normalized_phase = "unknown"
            trace.metadata["phase_coverage_gaps"] = list(trace.metadata.get("phase_coverage_gaps", ())) + ["unknown_worker_phase"]
        status = event.get("status", "unknown")
        if not isinstance(status, str) or status not in allowed_statuses:
            status = "unknown"
        kind = event.get("kind")
        actor_id = event.get("actor_id")
        provider = event.get("provider")
        model = event.get("model")
        terminal_reason = event.get("terminal_reason")
        metadata = {
            "observation_source": "worker_reported_not_authoritative",
            "input_digest": event.get("input_digest"),
            "output_ref": event.get("output_ref"),
            "evidence_refs": event.get("evidence_refs", ()),
            "observed_duration_ms": event.get("duration_ms"),
        }
        span = trace.span(
            name[:160], kind[:80] if isinstance(kind, str) else "observation",
            metadata=metadata, parent_id=worker_span.span_id, phase=normalized_phase,
            actor_id=actor_id[:120] if isinstance(actor_id, str) else None,
            provider=provider[:120] if isinstance(provider, str) else None,
            model=model[:160] if isinstance(model, str) else None,
            source_revision=source_revision,
        )
        span.finish(
            {"summary": event.get("summary", ""), "output_ref": event.get("output_ref")},
            status,
            terminal_reason[:500] if isinstance(terminal_reason, str) else None,
        )


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
    persona_workflow: PersonaWorkflow | None = None,
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
    if release_store is not None and regression_plan is None:
        raise ValueError("release_store requires a pinned regression_plan")
    tracer = tracer or Tracer()
    trace = tracer.start("auren.coding_task", {
        "task_id": task.task_id,
        "domain": task.artifact_type,
        "risk": task.risk,
        "mutation_mode": task.mutation_mode,
        "review_mode": "independent_personas" if persona_workflow is not None else "not_configured",
        "source_revision": task.source_revision,
        "config_digest": task.config_digest,
        "session_id": task.session_id,
        "run_id": task.run_id,
        "phase_coverage_gaps": [] if persona_workflow is not None else [
            "ideation", "research", "independent_plan_review", "independent_implementation_review"
        ],
    }, run_id=task.run_id, task_id=task.task_id, session_id=task.session_id,
       source_revision=task.source_revision or None, config_digest=task.config_digest or None)
    try:
        if persona_workflow is None and (task.risk.lower() in {"high", "critical"} or release_store is not None):
            raise ValueError("high-risk execution and release transitions require independent persona review")
        if persona_workflow is not None:
            persona_workflow.validate()

        plan_span = trace.span(
            "planning", "planner", {"request": task.request, "artifact_type": task.artifact_type},
            phase="planning", actor_id=persona_workflow.roster.planner.agent_id if persona_workflow else "auren.planner",
            source_revision=task.source_revision or None,
        )
        assignments = plan_task(task, registry=registry, support_limit=support_limit)
        approved_plan: EngineeringPlan | None = None
        worker_task = task
        result = ExecutionResult(task, assignments)
        result.trace_id = trace.trace_id
        result.ledger.append(LedgerEntry("planned", f"selected {len(assignments)} specialist(s)"))

        if persona_workflow is not None:
            approved_plan = persona_workflow.plan(task, assignments)
            if not isinstance(approved_plan, EngineeringPlan):
                raise TypeError("planning agent must return an EngineeringPlan")
            approved_plan.validate()
            if task.source_revision and approved_plan.source_revision != task.source_revision:
                raise ValueError("plan source revision does not match the task source revision")
            digest = canonical_plan_digest(approved_plan)
            if not approved_plan.plan_digest:
                approved_plan = replace(approved_plan, plan_digest=digest)
            trace.source_revision = approved_plan.source_revision
            trace.metadata["source_revision"] = approved_plan.source_revision
            plan_span.finish({
                "assignments": [a.__dict__ for a in assignments],
                "plan_digest": digest,
                "acceptance_criteria": approved_plan.acceptance_criteria,
                "dependencies": approved_plan.dependencies,
                "risks_and_mitigations": approved_plan.risks_and_mitigations,
                "decision_rationale": approved_plan.decision_rationale,
                "budget": approved_plan.budget,
            })
            coverage_gaps = trace.metadata.setdefault("phase_coverage_gaps", [])
            if approved_plan.hypotheses:
                ideation_span = trace.span(
                    "ideation", "reasoning", {"hypotheses": approved_plan.hypotheses,
                    "alternatives": approved_plan.alternatives_considered},
                    parent_id=plan_span.span_id, phase="ideation",
                    actor_id=persona_workflow.roster.planner.agent_id,
                    source_revision=approved_plan.source_revision,
                )
                ideation_span.finish({"hypothesis_count": len(approved_plan.hypotheses)})
            else:
                coverage_gaps.append("ideation")
            if approved_plan.research_evidence_refs:
                research_span = trace.span(
                    "research_evidence", "evidence_reference",
                    {"evidence_refs": approved_plan.research_evidence_refs,
                     "unresolved_questions": approved_plan.unresolved_questions},
                    parent_id=plan_span.span_id, phase="research",
                    actor_id=persona_workflow.roster.planner.agent_id,
                    source_revision=approved_plan.source_revision,
                )
                research_span.finish({
                    "evidence_ref_count": len(approved_plan.research_evidence_refs),
                    "unresolved_question_count": len(approved_plan.unresolved_questions),
                })
            else:
                coverage_gaps.append("research")
            if approved_plan.unresolved_questions:
                trace.metadata["unresolved_questions"] = list(approved_plan.unresolved_questions)
            plan_review_span = trace.span("independent_plan_review", "review", {
                "plan_digest": digest,
                "reviewer_id": persona_workflow.roster.plan_reviewer.agent_id,
            }, parent_id=plan_span.span_id, phase="planning",
               actor_id=persona_workflow.roster.plan_reviewer.agent_id,
               source_revision=approved_plan.source_revision)
            plan_review = persona_workflow.review_plan(task, approved_plan)
            if not isinstance(plan_review, PersonaReview):
                plan_review_span.finish({"error": "invalid_review_contract"}, "error")
                raise TypeError("plan reviewer must return a PersonaReview")
            plan_review_span.finish({
                "approved": plan_review.approved,
                "finding_count": len(plan_review.findings),
                "evidence_refs": plan_review.evidence_refs,
            }, "ok" if plan_review.approved else "blocked")
            trace.metadata["plan_digest"] = digest
            trace.metadata["plan_review"] = {
                "reviewer_id": plan_review.reviewer_id,
                "approved": plan_review.approved,
                "findings": list(plan_review.findings),
                "evidence_refs": list(plan_review.evidence_refs),
                "dimensions": dict(plan_review.dimensions),
            }
            require_approved_review(
                plan_review,
                reviewer=persona_workflow.roster.plan_reviewer,
                target=persona_workflow.roster.planner,
                stage="plan",
            )
            worker_task = replace(task, approved_plan=asdict(approved_plan))
            if task.context is not None:
                context_metadata = {
                    "context_type": type(task.context).__name__,
                    "context_digest": getattr(task.context, "snapshot_digest", None),
                    "token_estimate": getattr(task.context, "token_estimate", None),
                    "chunk_count": len(getattr(task.context, "chunks", ())) if isinstance(getattr(task.context, "chunks", ()), (list, tuple)) else None,
                }
                context_span = trace.span(
                    "context_assembly", "context_builder", metadata=context_metadata,
                    parent_id=plan_span.span_id, phase="context",
                    actor_id="auren.context_builder", source_revision=approved_plan.source_revision,
                )
                context_span.finish({"context_available": True})
            else:
                trace.metadata.setdefault("phase_coverage_gaps", []).append("context_assembly")
            result.ledger.append(LedgerEntry("plan_review_approved", digest, tuple(plan_review.evidence_refs)))
        else:
            plan_span.finish({"assignments": [a.__dict__ for a in assignments]})

        worker_span = trace.span("agent_execution", "agent", {
            "task_id": task.task_id,
            "specialists": [a.specialist for a in assignments],
            "implementer_id": persona_workflow.roster.implementer.agent_id if persona_workflow else None,
        }, phase="implementation",
           actor_id=persona_workflow.roster.implementer.agent_id if persona_workflow else "auren.implementation",
           source_revision=approved_plan.source_revision if approved_plan else (task.source_revision or None))
        try:
            raw = dict(worker(worker_task, assignments))
            _record_worker_observations(
                trace, worker_span, raw,
                approved_plan.source_revision if approved_plan else (task.source_revision or None),
            )
            worker_span.finish({
                "deliverable_count": len(raw.get("deliverables", ())),
                "verification_count": len(raw.get("verification", ())),
                "observation_count": min(100, len(raw.get("observability_events", ())))
                    if isinstance(raw.get("observability_events", ()), (list, tuple)) else 0,
            })
        except Exception as exc:
            worker_span.finish({"error": type(exc).__name__}, "error")
            raise

        if persona_workflow is not None:
            implementation_review_span = trace.span("independent_implementation_review", "review", {
                "plan_digest": approved_plan.plan_digest if approved_plan else None,
                "reviewer_id": persona_workflow.roster.implementation_reviewer.agent_id,
                "deliverable_count": len(raw.get("deliverables", ())),
                "verification_count": len(raw.get("verification", ())),
            }, parent_id=worker_span.span_id, phase="verification",
               actor_id=persona_workflow.roster.implementation_reviewer.agent_id,
               source_revision=approved_plan.source_revision if approved_plan else None)
            implementation_review = persona_workflow.review_implementation(worker_task, approved_plan, raw)
            if not isinstance(implementation_review, PersonaReview):
                implementation_review_span.finish({"error": "invalid_review_contract"}, "error")
                raise TypeError("implementation reviewer must return a PersonaReview")
            implementation_review_span.finish({
                "approved": implementation_review.approved,
                "finding_count": len(implementation_review.findings),
                "evidence_refs": implementation_review.evidence_refs,
            }, "ok" if implementation_review.approved else "blocked")
            trace.metadata["implementation_review"] = {
                "reviewer_id": implementation_review.reviewer_id,
                "approved": implementation_review.approved,
                "findings": list(implementation_review.findings),
                "evidence_refs": list(implementation_review.evidence_refs),
                "dimensions": dict(implementation_review.dimensions),
            }
            if not implementation_review.approved:
                trace.metadata["next_action"] = "reconcile artifact changes, create a bounded corrective task, and obtain a fresh independent review"
            require_approved_review(
                implementation_review,
                reviewer=persona_workflow.roster.implementation_reviewer,
                target=persona_workflow.roster.implementer,
                stage="implementation",
            )
            gates = dict(raw.get("hard_gates", {}))
            gates["independent_implementation_review"] = True
            raw["hard_gates"] = gates
            raw["verification"] = list(raw.get("verification", ())) + ["independent implementation review approved"]
            result.ledger.append(LedgerEntry(
                "implementation_review_approved",
                "independent review approved",
                tuple(implementation_review.evidence_refs),
            ))
        elif release_store is not None:
            raise ValueError("release transitions cannot proceed without independent persona review")

        result.deliverables = [str(x) for x in raw.get("deliverables", ())]
        result.verification = [str(x) for x in raw.get("verification", ())]
        result.dimensions = {str(k): int(v) for k, v in dict(raw.get("dimensions", {})).items()}
        result.hard_gates = {str(k): bool(v) for k, v in dict(raw.get("hard_gates", {})).items()}
        result.findings = list(raw.get("findings", ()))
        evidence_span = trace.span("evidence", "retrieval", {"count": len(raw.get("evidence", ()))},
                                   phase="research", source_revision=task.source_revision or None)
        for item in raw.get("evidence", ()):
            evidence = item if isinstance(item, EvidenceItem) else EvidenceItem(**item)
            result.evidence.append(evidence)
            result.ledger.append(LedgerEntry("evidence", evidence.claim, (evidence_id(evidence),)))
        evidence_span.finish({"evidence_count": len(result.evidence)})

        verify_span = trace.span("verification", "verifier", {"checks": result.verification},
                                 phase="verification", source_revision=task.source_revision or None)
        result.ledger.append(LedgerEntry("verified", "; ".join(result.verification)))
        result.receipt = evaluate(result.dimensions, result.hard_gates, result.findings, [e.claim for e in result.evidence], rubric=rubric)
        score = _receipt_score(result.receipt)
        verify_span.score("quality", score, "normalized quality receipt", "auren")
        verify_span.finish({"passed": bool(getattr(result.receipt, "passed", False)), "quality": score})
        trace.score("task_quality", score, "quality receipt", "auren")

        if regression_plan is not None:
            regression_span = trace.span("regression", "evaluation", {"dataset": regression_plan.dataset.name, "dataset_version": regression_plan.dataset.version},
                                         phase="evaluation", source_revision=task.source_revision or None)
            regression_run, decision = run_trace_regression_loop(
                trace,
                regression_plan,
                quality=score,
                hard_gates={
                    **result.hard_gates,
                    # Trace presence is not independent approval. Legacy runs remain
                    # evaluable but cannot promote without the configured review personas.
                    "independent_implementation_review": persona_workflow is not None,
                },
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
                release_phase = "rollback" if decision.action == "rollback" else "release"
                release_span = trace.span(
                    "release_transition", "release_controller",
                    {"decision": decision.action, "reason": decision.reason,
                     "artifact_ref": artifact_ref.artifact_id if artifact_ref else None},
                    parent_id=regression_span.span_id, phase=release_phase,
                    actor_id="auren.release_controller",
                    source_revision=approved_plan.source_revision if approved_plan else (task.source_revision or None),
                )
                result.release_state = apply_promotion_decision(release_store, decision, artifact_ref)
                release_span.finish({
                    "action": result.release_state.action,
                    "artifact_id": result.release_state.artifact_id,
                }, "ok", None)
                trace.metadata["release"] = result.release_state.as_dict()
                trace.score("release_applied", 1.0, "release state transition completed", "auren")
                result.ledger.append(LedgerEntry("release", f"{result.release_state.action}: {result.release_state.artifact_id or 'previous'}"))
        elif regression_result is not None and regression_id:
            regression_span = trace.span("regression", "evaluation", {"regression_id": regression_id},
                                         phase="evaluation", source_revision=task.source_revision or None)
            result.regression_link = correlate_trace_with_regression(trace, regression_id, regression_result)
            apply_regression_score(trace, result.regression_link)
            regression_span.score("regression_pass", 1.0 if result.regression_link.passed else 0.0, "linked regression outcome", "regression")
            regression_span.finish({"passed": result.regression_link.passed, "dataset_digest": result.regression_link.dataset_digest, "failures": result.regression_link.failures})
            result.ledger.append(LedgerEntry("regression", f"{regression_id}: {'passed' if result.regression_link.passed else 'failed'}"))

        tracer.end(trace)
        return result
    except ReviewRejectedError as exc:
        trace.metadata["terminal_reason"] = str(exc)[:1000]
        trace.metadata["review_rejected_stage"] = exc.stage
        tracer.end(trace, "blocked")
        raise
    except Exception as exc:
        trace.metadata["terminal_reason"] = type(exc).__name__
        tracer.end(trace, "error")
        raise
