"""Evidence-gated, end-to-end vibe-coding lifecycle for AUREN.

The lifecycle is intentionally provider-neutral: callers supply stage handlers,
while AUREN owns stage order, dependency blocking, evidence contracts, durable
resume validation, and production authorization checks.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from .orchestration import Graph, Node, NodeKind


class VibeStage(str, Enum):
    IDEA = "idea"
    RESEARCH = "research"
    DEFINE_USER = "define_user"
    PRD = "prd"
    TECH_STACK = "choose_tech_stack"
    ARCHITECTURE = "architecture"
    DESIGN = "design"
    PROJECT_RULES = "project_rules"
    TASK_BREAKDOWN = "task_breakdown"
    SETUP = "setup"
    DEVELOPMENT = "development"
    TESTING = "testing"
    SECURITY_REVIEW = "security_review"
    CODE_REVIEW = "code_review"
    PREVIEW_DEPLOYMENT = "preview_deployment"
    QA_TESTING = "qa_testing"
    PRODUCTION_DEPLOY = "production_deploy"
    MONITORING = "monitoring"


@dataclass(frozen=True)
class StageSpec:
    stage: VibeStage
    title: str
    purpose: str
    required_fields: tuple[str, ...]
    kind: NodeKind = NodeKind.AGENT
    risk: str = "medium"
    may_be_not_applicable: bool = False


STAGE_SPECS: tuple[StageSpec, ...] = (
    StageSpec(VibeStage.IDEA, "IDEA", "Clarify problem, desired outcome, constraints and success measures.", ("problem", "success_metrics")),
    StageSpec(VibeStage.RESEARCH, "RESEARCH", "Validate market/domain context, alternatives and competitors.", ("market_evidence", "competitors")),
    StageSpec(VibeStage.DEFINE_USER, "DEFINE THE USER", "Identify target users, needs, workflows and accessibility constraints.", ("personas", "user_needs")),
    StageSpec(VibeStage.PRD, "PRD", "Define scope, requirements, non-goals and acceptance criteria.", ("requirements", "acceptance_criteria")),
    StageSpec(VibeStage.TECH_STACK, "CHOOSE TECH STACK", "Choose tools and technologies with explicit trade-offs.", ("technology_decisions",)),
    StageSpec(VibeStage.ARCHITECTURE, "ARCHITECTURE", "Record components, interfaces, data flows and failure boundaries.", ("architecture_decisions",)),
    StageSpec(VibeStage.DESIGN, "DESIGN", "Create UX flows, UI states, wireframes or equivalent design artifacts.", ("design_artifacts",)),
    StageSpec(VibeStage.PROJECT_RULES, "PROJECT RULES", "Set coding, testing, security, accessibility and repository rules.", ("rules",)),
    StageSpec(VibeStage.TASK_BREAKDOWN, "TASK BREAKDOWN", "Create dependency-aware, verifiable implementation tasks.", ("tasks",)),
    StageSpec(VibeStage.SETUP, "SETUP", "Verify repository, toolchain, dependencies, secrets handling and repeatable setup.", ("repository", "environment")),
    StageSpec(VibeStage.DEVELOPMENT, "DEVELOPMENT", "Implement the planned behavior in small, reviewable increments.", ("artifacts",)),
    StageSpec(VibeStage.TESTING, "TESTING", "Run automated tests and record results against acceptance criteria.", ("tests_run",), kind=NodeKind.EVALUATOR),
    StageSpec(VibeStage.SECURITY_REVIEW, "SECURITY REVIEW", "Review vulnerabilities, secrets, permissions, dependencies and threat boundaries.", ("findings",), kind=NodeKind.EVALUATOR, risk="high"),
    StageSpec(VibeStage.CODE_REVIEW, "CODE REVIEW", "Independently review correctness, maintainability and regressions.", ("reviewer", "review_summary"), kind=NodeKind.HUMAN),
    StageSpec(VibeStage.PREVIEW_DEPLOYMENT, "PREVIEW DEPLOYMENT", "Deploy an isolated preview and verify its health and configuration.", ("deployment_receipt",), risk="high", may_be_not_applicable=True),
    StageSpec(VibeStage.QA_TESTING, "QA TESTING", "Validate end-to-end behavior, usability and user feedback against the PRD.", ("qa_results", "user_feedback"), kind=NodeKind.EVALUATOR),
    StageSpec(VibeStage.PRODUCTION_DEPLOY, "PRODUCTION DEPLOY", "Release the exact reviewed revision only with explicit Guard authorization.", ("release_receipt", "deployed_revision"), kind=NodeKind.HUMAN, risk="critical", may_be_not_applicable=True),
    StageSpec(VibeStage.MONITORING, "MONITORING", "Define and verify health signals, alerts, ownership and rollback thresholds.", ("signals", "alerts", "rollback_thresholds"), kind=NodeKind.EVALUATOR, risk="high"),
)
SPEC_BY_STAGE = {spec.stage: spec for spec in STAGE_SPECS}
STAGE_ORDER = tuple(spec.stage for spec in STAGE_SPECS)
_SCHEMA_VERSION = "auren-vibe-coding-checkpoint-v1"
_MAX_EVIDENCE_ITEMS = 200


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    if isinstance(value, Mapping):
        return bool(value)
    return value is not None


def validate_stage_output(stage: VibeStage, output: Any) -> tuple[bool, str]:
    """Validate stage output and evidence before allowing dependent stages."""
    if not isinstance(output, Mapping):
        return False, "stage handler must return a mapping"
    status = output.get("status", "completed")
    if status == "not_applicable":
        if not SPEC_BY_STAGE[stage].may_be_not_applicable:
            return False, f"{stage.value} is required and cannot be marked not applicable"
        if not isinstance(output.get("rationale"), str) or not output["rationale"].strip():
            return False, "not-applicable stages require a non-empty rationale"
        if not _nonempty(output.get("evidence")):
            return False, "not-applicable stages require evidence explaining the decision"
        return True, ""
    if status not in {"completed", "passed"}:
        return False, "stage status must be completed or passed"
    if not _nonempty(output.get("evidence")):
        return False, "stage must provide evidence"
    missing = [field for field in SPEC_BY_STAGE[stage].required_fields if not _nonempty(output.get(field))]
    if missing:
        return False, "missing required stage outputs: " + ", ".join(missing)

    if stage == VibeStage.TESTING and output.get("status") != "passed":
        return False, "testing must explicitly report status='passed'"
    if stage == VibeStage.SECURITY_REVIEW:
        if output.get("status") != "passed":
            return False, "security review must explicitly report status='passed'"
        if not isinstance(output.get("findings"), list):
            return False, "security findings must be a list, including an empty list when none are found"
        if output.get("critical_findings", 0) != 0:
            return False, "security review has unresolved critical findings"
    if stage == VibeStage.CODE_REVIEW:
        if output.get("status") != "passed":
            return False, "code review must explicitly report status='passed'"
        author = str(output.get("author", "")).strip()
        reviewer = str(output.get("reviewer", "")).strip()
        if author and reviewer and author.casefold() == reviewer.casefold():
            return False, "code review must be independent of the author"
        if output.get("blocking_findings", 0) != 0:
            return False, "code review has unresolved blocking findings"
    if stage == VibeStage.QA_TESTING:
        if output.get("status") != "passed":
            return False, "QA must explicitly report status='passed'"
        if not isinstance(output.get("user_feedback"), (str, list, Mapping)):
            return False, "QA must record user feedback or a documented feedback result"
    if stage == VibeStage.PRODUCTION_DEPLOY:
        approval = output.get("approval")
        if not isinstance(approval, Mapping) or approval.get("approved") is not True:
            return False, "production deployment requires an explicit approved release receipt"
        if not _nonempty(approval.get("approved_by")) or not _nonempty(approval.get("approved_at")):
            return False, "production approval must identify approver and timestamp"
        expected_revision = str(output.get("reviewed_revision", "")).strip()
        deployed_revision = str(output.get("deployed_revision", "")).strip()
        if not expected_revision or expected_revision != deployed_revision:
            return False, "deployed revision must exactly match the reviewed revision"
    if stage == VibeStage.MONITORING:
        for field in ("signals", "alerts", "rollback_thresholds"):
            if not isinstance(output.get(field), list) or not output[field]:
                return False, f"monitoring requires a non-empty {field} list"
    return True, ""


def build_vibe_coding_graph(
    handlers: Mapping[str | VibeStage, Callable[[Mapping[str, Any]], Mapping[str, Any]]],
) -> Graph:
    """Build AUREN's canonical 18-stage graph around caller-supplied handlers.

    Handler keys are VibeStage values or their string values. Every stage must
    have a handler; there are no implicit no-op handlers. Only preview and
    production deployment may be explicitly marked not applicable. Production
    deployment handlers are never invoked without an authorization envelope,
    unless the caller explicitly disables production deployment with a rationale.
    """
    normalized: dict[VibeStage, Callable[[Mapping[str, Any]], Mapping[str, Any]]] = {}
    for key, handler in handlers.items():
        try:
            stage = key if isinstance(key, VibeStage) else VibeStage(str(key))
        except ValueError as exc:
            raise ValueError(f"unknown vibe-coding stage handler: {key}") from exc
        if stage in normalized:
            raise ValueError(f"duplicate handler for stage {stage.value}")
        if not callable(handler):
            raise ValueError(f"handler for {stage.value} must be callable")
        normalized[stage] = handler
    missing = [stage.value for stage in STAGE_ORDER if stage not in normalized]
    if missing:
        raise ValueError("all 18 vibe-coding stage handlers are required; missing: " + ", ".join(missing))

    nodes: list[Node] = []
    previous: str | None = None
    for spec in STAGE_SPECS:
        stage = spec.stage

        def run_stage(state: Mapping[str, Any], *, _stage: VibeStage = stage,
                      _handler: Callable[[Mapping[str, Any]], Mapping[str, Any]] = normalized[stage]) -> Mapping[str, Any]:
            if _stage == VibeStage.PRODUCTION_DEPLOY:
                disabled = state.get("production_deployment_required") is False
                if disabled:
                    rationale = state.get("production_deployment_rationale")
                    if not isinstance(rationale, str) or not rationale.strip():
                        raise PermissionError("disabling production deployment requires an explicit rationale")
                    return {
                        "status": "not_applicable",
                        "rationale": rationale,
                        "evidence": ["production deployment explicitly disabled by task policy"],
                    }
                approval = state.get("production_approval")
                if not isinstance(approval, Mapping) or approval.get("approved") is not True:
                    raise PermissionError("production deployment blocked: explicit production_approval is required")
                if not _nonempty(approval.get("approved_by")) or not _nonempty(approval.get("approved_at")):
                    raise PermissionError("production deployment blocked: approver and timestamp are required")
            if _stage == VibeStage.PREVIEW_DEPLOYMENT and state.get("preview_deployment_required") is False:
                rationale = state.get("preview_deployment_rationale")
                if not isinstance(rationale, str) or not rationale.strip():
                    raise PermissionError("disabling preview deployment requires an explicit rationale")
                return {
                    "status": "not_applicable",
                    "rationale": rationale,
                    "evidence": ["preview deployment explicitly disabled by task policy"],
                }
            result = _handler(state)
            if not isinstance(result, Mapping):
                raise TypeError(f"{_stage.value} handler must return a mapping")
            valid, reason = validate_stage_output(_stage, result)
            if not valid:
                raise ValueError(f"{_stage.value} stage gate failed: {reason}")
            # Copy the returned envelope so later handler mutation cannot change
            # the evidence already consumed by dependent stages.
            return json.loads(json.dumps(dict(result), sort_keys=True, allow_nan=False))

        nodes.append(Node(
            name=stage.value,
            kind=spec.kind,
            run=run_stage,
            depends_on=(previous,) if previous else (),
            max_attempts=1,
            evaluator=lambda output, _stage=stage: validate_stage_output(_stage, output)[0],
            critical=True,
            risk=spec.risk,
            inputs=("intent",) if previous is None else (previous,),
            outputs=("evidence",),
        ))
        previous = stage.value
    return Graph(nodes)


@dataclass(frozen=True)
class VibeCodingCheckpoint:
    """Versioned workflow receipt for durable pause/resume across process restarts."""

    workflow_id: str
    completed: tuple[str, ...]
    outputs: Mapping[str, Mapping[str, Any]]
    blocked_stage: str | None = None
    schema_version: str = _SCHEMA_VERSION
    digest: str = ""

    def snapshot(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "workflow_id": self.workflow_id,
            "completed": list(self.completed),
            "outputs": {key: dict(value) for key, value in sorted(self.outputs.items())},
            "blocked_stage": self.blocked_stage,
        }
        payload["digest"] = _digest(payload)
        return payload

    @classmethod
    def resume(cls, raw: Mapping[str, Any]) -> "VibeCodingCheckpoint":
        if not isinstance(raw, Mapping) or raw.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("unsupported vibe-coding checkpoint schema")
        supplied = raw.get("digest")
        payload = {key: value for key, value in raw.items() if key != "digest"}
        if not isinstance(supplied, str) or supplied != _digest(payload):
            raise ValueError("vibe-coding checkpoint digest mismatch")
        workflow_id = raw.get("workflow_id")
        completed = raw.get("completed")
        outputs = raw.get("outputs")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise ValueError("checkpoint workflow_id is required")
        if not isinstance(completed, list) or any(not isinstance(item, str) for item in completed):
            raise ValueError("checkpoint completed stages must be a list of strings")
        if len(completed) != len(set(completed)):
            raise ValueError("checkpoint has duplicate completed stages")
        expected_prefix = [stage.value for stage in STAGE_ORDER[:len(completed)]]
        if completed != expected_prefix:
            raise ValueError("checkpoint completed stages are not a valid ordered prefix")
        if not isinstance(outputs, Mapping) or set(outputs) != set(completed):
            raise ValueError("checkpoint outputs must match completed stages exactly")
        for name in completed:
            try:
                stage = VibeStage(name)
            except ValueError as exc:
                raise ValueError(f"checkpoint contains unknown stage: {name}") from exc
            valid, reason = validate_stage_output(stage, outputs[name])
            if not valid:
                raise ValueError(f"checkpoint stage {name} failed validation: {reason}")
        blocked_stage = raw.get("blocked_stage")
        if blocked_stage is not None and blocked_stage not in {stage.value for stage in STAGE_ORDER}:
            raise ValueError("checkpoint blocked_stage is unknown")
        return cls(
            workflow_id=workflow_id,
            completed=tuple(completed),
            outputs={key: dict(value) for key, value in outputs.items()},
            blocked_stage=blocked_stage,
            schema_version=_SCHEMA_VERSION,
            digest=supplied,
        )


__all__ = [
    "STAGE_ORDER", "STAGE_SPECS", "StageSpec", "VibeCodingCheckpoint",
    "VibeStage", "build_vibe_coding_graph", "validate_stage_output",
]
