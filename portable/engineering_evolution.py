"""Integrated twelve-phase engineering evolution control plane.

This module composes the existing AER primitives rather than replacing them.
It adds durable coordination for evidence graphs, predictive feedback,
compaction, impact/failure prediction, historical decomposition, provider
calibration, cross-project transfer validation, autonomy graduation, benchmark
readiness, and local execution readiness.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

from .context_graph import ContextEdge, ContextGraph, ContextNode
from .engineering_evidence_envelope import EngineeringEvidenceEnvelope
from .impact_analysis import ImpactReport, analyze
from .persistent_memory import PersistentMemory
from .persistent_remediation_backlog import PersistentRemediationBacklog
from .provider_fabric import ProviderFabric
from .task_planner import Task, TaskPlan


PHASES = (
    "canonical_engineering_evidence_envelope",
    "end_to_end_evidence_graph",
    "predictive_world_model_feedback",
    "aer_aware_context_compaction",
    "repository_change_impact_prediction",
    "failure_pattern_prediction",
    "historical_task_decomposition",
    "model_provider_performance_calibration",
    "cross_project_learning_validation",
    "engineering_capability_graduation",
    "full_autonomous_engineering_benchmark",
    "production_local_llm_execution",
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class EvidenceGraphResult:
    envelope_id: str
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    graph_digest: str


@dataclass(frozen=True)
class EvidenceGraphIntegrity:
    valid: bool
    missing_nodes: tuple[str, ...] = ()
    missing_edges: tuple[str, ...] = ()
    duplicate_nodes: tuple[str, ...] = ()
    digest: str = ""


@dataclass(frozen=True)
class WorldFeedback:
    prediction_id: str
    prediction_error: str
    calibrated_confidence: float
    prediction_accuracy: float
    sample_count: int
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class CompactionResult:
    representation: str
    original_items: int
    retained_items: int
    omitted_items: int
    digest: str


@dataclass(frozen=True)
class FailurePrediction:
    task_family: str
    capability: str
    probability: float
    sample_count: int
    reasons: tuple[str, ...]
    recommended_controls: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalDecomposition:
    plan: TaskPlan
    source_findings: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class ProviderCalibration:
    provider: str
    capability: str
    samples: int
    success_rate: float
    mean_duration_seconds: float
    mean_quality: float
    confidence: float


@dataclass(frozen=True)
class CrossProjectValidation:
    capability: str
    source_project: str
    target_project: str
    samples: int
    transfer_rate: float
    regressions: int
    accepted: bool
    reason: str


@dataclass(frozen=True)
class LocalExecutionReadiness:
    ready: bool
    backend: str
    model_path: str
    ollama_independent: bool
    reasons: tuple[str, ...]


class EngineeringEvolutionControlPlane:
    """Coordinates all twelve phases with durable, fail-closed state."""

    def __init__(self, memory: PersistentMemory, project: str) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be PersistentMemory")
        if not project.strip():
            raise ValueError("project is required")
        self.memory = memory
        self.project = project
        self.backlog = PersistentRemediationBacklog(memory, project)
        self.graph = ContextGraph(memory, project)
        self.providers = ProviderFabric()
        with self.memory._lock, self.memory._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS evolution_metrics(
              project TEXT NOT NULL, phase TEXT NOT NULL, key TEXT NOT NULL,
              value TEXT NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY(project,phase,key));
            CREATE TABLE IF NOT EXISTS provider_calibration(
              project TEXT NOT NULL, provider TEXT NOT NULL, capability TEXT NOT NULL,
              success INTEGER NOT NULL, duration REAL NOT NULL, quality REAL NOT NULL,
              created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS transfer_validation(
              source_project TEXT NOT NULL, target_project TEXT NOT NULL,
              capability TEXT NOT NULL, success INTEGER NOT NULL,
              regression INTEGER NOT NULL, created_at TEXT NOT NULL);
            """)

    # Phase 1: canonical evidence envelope.
    def envelope(self, context_evidence: Any) -> EngineeringEvidenceEnvelope:
        return EngineeringEvidenceEnvelope.from_context_evidence(context_evidence)

    # Phase 2: turn the envelope into an end-to-end graph without copying claims.
    def evidence_graph(self, envelope: EngineeringEvidenceEnvelope) -> EvidenceGraphResult:
        """Materialize complete lifecycle lineage as a bounded relationship graph."""
        root = f"envelope:{envelope.envelope_digest}"
        nodes = [
            ContextNode(root, "evidence_envelope", envelope.task_id, "engineering_evidence_envelope"),
            ContextNode(f"intent:{envelope.intent_digest}", "intent", envelope.intent_digest, "intent"),
            ContextNode(f"repo:{envelope.repository_snapshot_digest}", "repository_snapshot", envelope.repository_snapshot_digest, "repository"),
        ]
        edges: list[ContextEdge] = []
        for ref in envelope.evidence:
            nodes.append(ContextNode(f"evidence:{ref.evidence_id}", "evidence", ref.evidence_id, "canonical_evidence",
                                      properties={"snapshot": ref.snapshot, "freshness": ref.freshness}))
        lifecycle = (
            ("decision", envelope.decision_ids),
            ("changeset", (envelope.changeset_id,) if envelope.changeset_id else ()),
            ("verification", envelope.verification_ids),
            ("review", envelope.review_ids),
            ("regression", envelope.regression_ids),
            ("release", envelope.release_ids),
            ("outcome", (envelope.outcome_id,) if envelope.outcome_id else ()),
        )
        for kind, ids in lifecycle:
            for identifier in ids:
                nodes.append(ContextNode(f"{kind}:{identifier}", kind, identifier, "engineering_lifecycle"))
        for node in nodes:
            self.graph.upsert_node(node)
        edges.append(self.graph.link(root, "has_intent", f"intent:{envelope.intent_digest}", source="envelope"))
        edges.append(self.graph.link(root, "uses_snapshot", f"repo:{envelope.repository_snapshot_digest}", source="envelope"))
        for ref in envelope.evidence:
            edges.append(self.graph.link(root, "references_evidence", f"evidence:{ref.evidence_id}", source="envelope"))
        previous = root
        for kind, ids in lifecycle:
            for identifier in ids:
                node_id = f"{kind}:{identifier}"
                edges.append(self.graph.link(previous, f"produces_{kind}", node_id, source="lifecycle"))
                previous = node_id
        result = EvidenceGraphResult(envelope.envelope_digest, tuple(n.node_id for n in nodes),
                                     tuple(e.edge_id for e in edges), self.graph.digest())
        integrity = self.validate_evidence_graph(envelope, result)
        if not integrity.valid:
            raise ValueError(f"evidence graph integrity failure: missing_nodes={integrity.missing_nodes}, missing_edges={integrity.missing_edges}")
        return result

    def validate_evidence_graph(self, envelope: EngineeringEvidenceEnvelope,
                                result: EvidenceGraphResult) -> EvidenceGraphIntegrity:
        """Validate that every envelope lifecycle reference has graph lineage."""
        expected_nodes = {
            f"envelope:{envelope.envelope_digest}",
            f"intent:{envelope.intent_digest}",
            f"repo:{envelope.repository_snapshot_digest}",
            *(f"evidence:{ref.evidence_id}" for ref in envelope.evidence),
        }
        lifecycle = (
            ("decision", envelope.decision_ids), ("changeset", (envelope.changeset_id,) if envelope.changeset_id else ()),
            ("verification", envelope.verification_ids), ("review", envelope.review_ids),
            ("regression", envelope.regression_ids), ("release", envelope.release_ids),
            ("outcome", (envelope.outcome_id,) if envelope.outcome_id else ()),
        )
        for kind, ids in lifecycle:
            expected_nodes.update(f"{kind}:{identifier}" for identifier in ids)
        node_set = set(result.node_ids)
        missing_nodes = tuple(sorted(expected_nodes - node_set))
        duplicate_nodes = tuple(sorted({x for x in result.node_ids if result.node_ids.count(x) > 1}))
        expected_edge_count = 2 + len(envelope.evidence)
        expected_edge_count += sum(len(ids) for _, ids in lifecycle)
        missing_edges = (f"expected_at_least_{expected_edge_count}_edges",) if len(result.edge_ids) < expected_edge_count else ()
        valid = not missing_nodes and not missing_edges and not duplicate_nodes and bool(result.graph_digest)
        return EvidenceGraphIntegrity(valid, missing_nodes, missing_edges, duplicate_nodes, result.graph_digest)

    # Phase 3: predictive world feedback is recorded by the existing WorldModel.
    def record_world_feedback(self, world_model: Any, prediction: Any, actual: Any, *, evidence: Iterable[str] = ()) -> WorldFeedback:
        if not hasattr(world_model, "score_prediction") or not hasattr(world_model, "prediction_calibration"):
            raise TypeError("world_model must expose score_prediction and prediction_calibration")
        evidence_ids = tuple(dict.fromkeys(str(x).strip() for x in evidence if str(x).strip()))
        if not evidence_ids:
            raise ValueError("verified world feedback requires evidence")
        error = world_model.score_prediction(prediction, actual)
        predicate = getattr(prediction, "predicate", None)
        action = getattr(prediction, "action", None)
        calibration = world_model.prediction_calibration(predicate=predicate, action=action)
        accuracy = float(calibration.get("accuracy", 0.0))
        samples = int(calibration.get("samples", 0))
        confidence = float(getattr(prediction, "confidence", 0.0))
        adjusted = round((confidence + accuracy) / 2.0, 4) if samples else round(confidence, 4)
        return WorldFeedback(str(getattr(prediction, "prediction_id", "")),
                             str(getattr(error, "error_digest", "")), adjusted,
                             round(accuracy, 4), samples, evidence_ids)

    # Phase 4: compact evidence using AER-like stable key/value rows.
    def compact_context(self, items: Sequence[Mapping[str, Any]], *, budget: int = 12000) -> CompactionResult:
        if budget < 128:
            raise ValueError("budget must be at least 128")
        ranked = sorted(
            (dict(x) for x in items),
            key=lambda x: (-float(x.get("confidence", 0.0)), str(x.get("evidence_id", x.get("id", "")))),
        )
        required = [item for item in ranked if bool(item.get("required", False))]
        optional = [item for item in ranked if not bool(item.get("required", False))]
        rows: list[str] = []
        used = 0
        for item in (*required, *optional):
            compact = "|".join(f"{k}={json.dumps(item[k], ensure_ascii=False, separators=(',', ':'))}" for k in sorted(item))
            if used + len(compact) + 1 > budget:
                if item in required:
                    raise ValueError("context budget is too small for required evidence")
                continue
            rows.append(compact)
            used += len(compact) + 1
        representation = "\n".join(rows)
        return CompactionResult(representation, len(items), len(rows), len(items) - len(rows), _digest(representation))

    # Phase 5: deterministic repository impact prediction delegates to existing analyzer.
    def change_impact(self, root: str | Path, changed: Iterable[str]) -> ImpactReport:
        return analyze(Path(root), tuple(changed))

    def impact_gate(self, root: str | Path, changed: Iterable[str], *, critical_approved: bool = False) -> ImpactReport:
        """Run impact analysis and fail closed on unapproved critical shared changes."""
        changed_paths = tuple(str(x) for x in changed)
        if not changed_paths:
            raise ValueError("at least one changed path is required")
        report = self.change_impact(root, changed_paths)
        if report.review_required and not critical_approved:
            critical = [item.path for item in report.impacted if item.review_level == "critical"]
            if critical:
                raise PermissionError("critical impact requires explicit approval: " + ", ".join(critical))
        return report

    # Phase 6: predict failures before execution from persistent remediation history.
    def failure_gate(self, *, task_family: str, capability: str, threshold: float = 0.5,
                     approved: bool = False) -> FailurePrediction:
        """Predict historical failure risk and fail closed above the threshold."""
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        prediction = self.failure_prediction(task_family=task_family, capability=capability)
        if prediction.probability >= threshold and not approved:
            raise PermissionError(
                f"historical failure gate requires approval: {task_family}/{capability} "
                f"probability={prediction.probability:.3f}"
            )
        return prediction

    def failure_prediction(self, *, task_family: str, capability: str) -> FailurePrediction:
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute(
                "SELECT severity,status,attempts FROM remediation_backlog WHERE project=? AND task_family=? AND capability=?",
                (self.project, task_family, capability),
            ).fetchall()
        if not rows:
            return FailurePrediction(task_family, capability, 0.0, 0, ("no historical failures",), ("normal verification",))
        severe = sum(1 for severity,_,_ in rows if str(severity).lower() in {"blocker","critical","high"})
        attempts = sum(int(r[2]) for r in rows)
        unresolved = sum(1 for _,status,_ in rows if str(status) in {"pending","in_progress","blocked","deferred"})
        probability = min(0.95, (0.25 * severe + 0.15 * unresolved + 0.05 * attempts) / max(1, len(rows)))
        controls = ["impact analysis", "targeted regression", "independent review"]
        if probability >= 0.5:
            controls.append("pre-execution failure gate")
        return FailurePrediction(task_family, capability, round(probability, 4), len(rows),
                                  (f"{severe} severe findings", f"{unresolved} unresolved findings", f"{attempts} repair attempts"),
                                  tuple(controls))

    # Phase 7: derive a dependency-safe task plan from recurring remediation families.
    def historical_decomposition(self, *, task_family: str, capability: str) -> HistoricalDecomposition:
        findings = self.backlog.pending(limit=100, task_family=task_family, capability=capability)
        tasks: list[Task] = []
        for index, finding in enumerate(findings):
            tasks.append(Task(
                id=f"remediate-{finding.finding_id}",
                title=finding.title,
                description=finding.recommendation,
                priority="high" if finding.severity in {"blocker","critical","high"} else "medium",
                tags=["historical-remediation", capability],
                acceptance=["verification evidence recorded", "finding status resolved"],
                metadata={"finding_id": finding.finding_id, "historical_attempts": finding.attempts},
                parallel_group=f"remediation-{task_family}",
                verification_strategy=["targeted regression", "independent review"],
            ))
        return HistoricalDecomposition(TaskPlan(tasks), tuple(x.finding_id for x in findings),
                                       min(1.0, len(findings) / 10.0) if findings else 0.0)

    # Phase 8: persistent provider performance calibration.
    def record_provider_result(self, provider: str, capability: str, *, success: bool, duration_seconds: float, quality: float) -> ProviderCalibration:
        if not provider.strip() or not capability.strip() or duration_seconds < 0 or not 0 <= quality <= 1:
            raise ValueError("invalid provider result")
        with self.memory._lock, self.memory._connect() as db:
            db.execute("INSERT INTO provider_calibration VALUES(?,?,?,?,?,?,?)",
                       (self.project, provider, capability, int(success), float(duration_seconds), float(quality), _utc()))
        return self.provider_calibration(provider, capability)

    def provider_calibration(self, provider: str, capability: str) -> ProviderCalibration:
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute("SELECT success,duration,quality FROM provider_calibration WHERE project=? AND provider=? AND capability=?",
                              (self.project, provider, capability)).fetchall()
        samples = len(rows)
        if not samples:
            return ProviderCalibration(provider, capability, 0, 0.0, 0.0, 0.0, 0.0)
        confidence = min(1.0, samples / 10.0)
        return ProviderCalibration(provider, capability, samples, round(mean(r[0] for r in rows), 4),
                                   round(mean(r[1] for r in rows), 4), round(mean(r[2] for r in rows), 4),
                                   round(confidence, 4))

    # Phase 9: validate transfer against held-out target-project episodes.
    def record_transfer_result(self, source_project: str, target_project: str, capability: str, *, success: bool, regression: bool = False) -> CrossProjectValidation:
        with self.memory._lock, self.memory._connect() as db:
            db.execute("INSERT INTO transfer_validation VALUES(?,?,?,?,?,?)",
                       (source_project, target_project, capability, int(success), int(regression), _utc()))
        return self.cross_project_validation(source_project, target_project, capability)

    def cross_project_validation(self, source_project: str, target_project: str, capability: str) -> CrossProjectValidation:
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute("SELECT success,regression FROM transfer_validation WHERE source_project=? AND target_project=? AND capability=?",
                              (source_project, target_project, capability)).fetchall()
        samples = len(rows)
        rate = mean(r[0] for r in rows) if rows else 0.0
        regressions = sum(r[1] for r in rows)
        accepted = samples >= 5 and rate >= 0.8 and regressions == 0
        reason = "held-out transfer gate passed" if accepted else "insufficient transfer evidence or regression observed"
        return CrossProjectValidation(capability, source_project, target_project, samples, round(rate,4), regressions, accepted, reason)

    # Phase 10: expose graduation evidence while keeping authority with AutonomyGraduator.
    def graduation_ready(self, evaluation: Any, self_profile: Any, *, regression_passed: bool, safety_reviewed: bool, human_approved: bool) -> Any:
        from .autonomy_graduation import AutonomyEvidence, AutonomyGraduator, GraduationPolicy
        evidence = AutonomyEvidence(evaluation, self_profile, regression_passed, safety_reviewed, human_approved)
        return AutonomyGraduator().evaluate("graduated", evidence, GraduationPolicy())

    # Phase 11: deterministic benchmark gate for autonomous engineering.
    def benchmark_gate(self, benchmark_result: Any, *, min_success: float = 0.9, min_transfer: float = 0.8) -> bool:
        if not 0 <= min_success <= 1 or not 0 <= min_transfer <= 1:
            raise ValueError("benchmark thresholds must be between 0 and 1")
        success = float(getattr(benchmark_result, "success_rate", 0.0))
        transfer = getattr(benchmark_result, "transfer_rate", None)
        return success >= min_success and (transfer is None or float(transfer) >= min_transfer)

    # Phase 12: local path is independent of Ollama when an embedded GGUF is configured.
    def local_execution_readiness(self, config: Any | None = None) -> LocalExecutionReadiness:
        from .local_llm import LocalLLMConfig, embedded_available
        cfg = config or LocalLLMConfig.from_env()
        embedded = bool(cfg.model_path) and embedded_available(cfg)
        reasons: list[str] = []
        if embedded:
            return LocalExecutionReadiness(True, "embedded", cfg.model_path, True, ())
        reasons.append("embedded llama.cpp backend unavailable")
        if not cfg.model_path:
            reasons.append("AER_LOCAL_LLM_MODEL_PATH is not configured")
        return LocalExecutionReadiness(False, "ollama", cfg.model_path, False, tuple(reasons))

    def status(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "phases": list(PHASES),
            "unresolved_remediation": len(self.backlog.pending(limit=10000)),
            "graph_digest": self.graph.digest(),
            "provider_calibration": True,
            "cross_project_validation": True,
            "local_llm_embedded_supported": True,
        }


__all__ = [
    "PHASES", "EngineeringEvolutionControlPlane", "EvidenceGraphResult",
    "WorldFeedback", "CompactionResult", "FailurePrediction",
    "HistoricalDecomposition", "ProviderCalibration", "CrossProjectValidation",
    "LocalExecutionReadiness", "EvidenceGraphIntegrity",
]
