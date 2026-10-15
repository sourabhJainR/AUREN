"""Framework-neutral orchestration contracts and evidence-based comparisons.

This module describes workflow intent and aggregates adapter-produced trial
records. It does not execute agents, attest evaluator independence, or establish
security isolation. Those remain host/Arena responsibilities.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from statistics import median
from typing import Any, Iterable, Mapping


SPEC_VERSION = "auren-orchestration-spec-v1"
NODE_KINDS = frozenset({"agent", "deterministic", "evaluator", "human", "router", "join"})
SPLITS = frozenset({"development", "calibration", "sealed_holdout"})
_HEX = frozenset("0123456789abcdef")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _finite_positive(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")


def _sha256(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class WorkflowNodeSpec:
    node_id: str
    kind: str
    capability: str
    max_attempts: int = 1
    side_effect: bool = False
    idempotent: bool = False
    timeout_seconds: float | None = None
    required_evidence: tuple[str, ...] = ()

    def validate(self, global_attempt_budget: int) -> None:
        _text(self.node_id, "node_id")
        _text(self.capability, f"{self.node_id}.capability")
        if self.kind not in NODE_KINDS:
            raise ValueError(f"unsupported node kind: {self.kind}")
        _positive_int(self.max_attempts, f"{self.node_id}.max_attempts")
        if self.max_attempts > global_attempt_budget:
            raise ValueError(f"{self.node_id}.max_attempts exceeds global attempt budget")
        if type(self.side_effect) is not bool or type(self.idempotent) is not bool:
            raise ValueError("side_effect and idempotent must be booleans")
        if self.side_effect and self.max_attempts > 1 and not self.idempotent:
            raise ValueError(f"{self.node_id} retries a non-idempotent side effect")
        if self.timeout_seconds is not None:
            _finite_positive(self.timeout_seconds, f"{self.node_id}.timeout_seconds")
        if len(set(self.required_evidence)) != len(self.required_evidence):
            raise ValueError(f"{self.node_id}.required_evidence must be unique")
        for item in self.required_evidence:
            _text(item, f"{self.node_id}.required_evidence")


@dataclass(frozen=True)
class WorkflowEdgeSpec:
    source: str
    target: str
    condition: str = "success"
    max_traversals: int | None = None

    def validate(self) -> None:
        _text(self.source, "edge.source")
        _text(self.target, "edge.target")
        _text(self.condition, "edge.condition")
        if self.max_traversals is not None:
            _positive_int(self.max_traversals, "edge.max_traversals")


@dataclass(frozen=True)
class OrchestrationSpec:
    workflow_id: str
    nodes: tuple[WorkflowNodeSpec, ...]
    edges: tuple[WorkflowEdgeSpec, ...]
    entry_nodes: tuple[str, ...]
    policy_digest: str
    max_steps: int = 100
    max_attempts: int = 12
    deadline_seconds: float = 900.0
    spec_version: str = SPEC_VERSION

    def validate(self) -> None:
        if self.spec_version != SPEC_VERSION:
            raise ValueError(f"unsupported orchestration spec version: {self.spec_version}")
        _text(self.workflow_id, "workflow_id")
        _sha256(self.policy_digest, "policy_digest")
        _positive_int(self.max_steps, "max_steps")
        _positive_int(self.max_attempts, "max_attempts")
        _finite_positive(self.deadline_seconds, "deadline_seconds")
        if not self.nodes:
            raise ValueError("at least one workflow node is required")
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("workflow node IDs must be unique")
        known = set(node_ids)
        for node in self.nodes:
            node.validate(self.max_attempts)
        if not self.entry_nodes or len(set(self.entry_nodes)) != len(self.entry_nodes):
            raise ValueError("entry_nodes must be non-empty and unique")
        if any(entry not in known for entry in self.entry_nodes):
            raise ValueError("entry_nodes references an unknown node")
        edge_keys = [(edge.source, edge.target, edge.condition) for edge in self.edges]
        if len(edge_keys) != len(set(edge_keys)):
            raise ValueError("workflow edges must be unique")
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
        for edge in self.edges:
            edge.validate()
            if edge.source not in known or edge.target not in known:
                raise ValueError("workflow edge references an unknown node")
            adjacency[edge.source].append(edge.target)

        reachable: set[str] = set()
        pending = list(self.entry_nodes)
        while pending:
            current = pending.pop()
            if current in reachable:
                continue
            reachable.add(current)
            pending.extend(adjacency[current])
        if reachable != known:
            raise ValueError(f"workflow contains unreachable nodes: {sorted(known - reachable)}")

        def reaches(start: str, goal: str) -> bool:
            todo, seen = [start], set()
            while todo:
                current = todo.pop()
                if current == goal:
                    return True
                if current in seen:
                    continue
                seen.add(current)
                todo.extend(adjacency[current])
            return False

        for edge in self.edges:
            if reaches(edge.target, edge.source) and edge.max_traversals is None:
                raise ValueError(
                    f"cyclic edge {edge.source}->{edge.target} must declare max_traversals"
                )

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "spec_version": self.spec_version,
            "workflow_id": self.workflow_id,
            "entry_nodes": sorted(self.entry_nodes),
            "policy_digest": self.policy_digest,
            "max_steps": self.max_steps,
            "max_attempts": self.max_attempts,
            "deadline_seconds": self.deadline_seconds,
            "nodes": [
                {**asdict(node), "required_evidence": list(node.required_evidence)}
                for node in sorted(self.nodes, key=lambda item: item.node_id)
            ],
            "edges": [
                asdict(edge) for edge in sorted(self.edges, key=lambda item: (item.source, item.target, item.condition))
            ],
        }

    def digest(self) -> str:
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OrchestrationSpec":
        expected = {
            "spec_version", "workflow_id", "nodes", "edges", "entry_nodes",
            "policy_digest", "max_steps", "max_attempts", "deadline_seconds",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("orchestration spec has unknown or missing fields")
        if not isinstance(value["nodes"], list) or not isinstance(value["edges"], list):
            raise ValueError("nodes and edges must be arrays")
        if not isinstance(value["entry_nodes"], list):
            raise ValueError("entry_nodes must be an array")
        node_fields = {
            "node_id", "kind", "capability", "max_attempts", "side_effect",
            "idempotent", "timeout_seconds", "required_evidence",
        }
        edge_fields = {"source", "target", "condition", "max_traversals"}
        nodes = []
        for raw in value["nodes"]:
            if not isinstance(raw, Mapping) or set(raw) != node_fields:
                raise ValueError("workflow node has unknown or missing fields")
            if not isinstance(raw["required_evidence"], list):
                raise ValueError("required_evidence must be an array")
            nodes.append(WorkflowNodeSpec(
                node_id=raw["node_id"], kind=raw["kind"], capability=raw["capability"],
                max_attempts=raw["max_attempts"], side_effect=raw["side_effect"],
                idempotent=raw["idempotent"], timeout_seconds=raw["timeout_seconds"],
                required_evidence=tuple(raw["required_evidence"]),
            ))
        edges = []
        for raw in value["edges"]:
            if not isinstance(raw, Mapping) or set(raw) != edge_fields:
                raise ValueError("workflow edge has unknown or missing fields")
            edges.append(WorkflowEdgeSpec(**raw))
        spec = cls(
            spec_version=value["spec_version"], workflow_id=value["workflow_id"],
            nodes=tuple(nodes), edges=tuple(edges), entry_nodes=tuple(value["entry_nodes"]),
            policy_digest=value["policy_digest"], max_steps=value["max_steps"],
            max_attempts=value["max_attempts"], deadline_seconds=value["deadline_seconds"],
        )
        spec.validate()
        return spec


@dataclass(frozen=True)
class OrchestrationTrial:
    trial_id: str
    framework: str
    task_family: str
    split: str
    passed: bool
    unauthorized_actions: int
    trace_complete: bool
    recovery_expected: bool
    recovered: bool | None
    duration_ms: float
    cost_usd: float
    repeat_group: str
    outcome_digest: str
    evidence_digest: str
    # Optional for historical receipts; promotion requires all three bindings.
    workflow_digest: str = ""
    benchmark_digest: str = ""
    policy_digest: str = ""
    termination_bounded: bool = False

    def validate(self) -> None:
        for name in ("trial_id", "framework", "task_family", "repeat_group"):
            _text(getattr(self, name), name)
        if self.split not in SPLITS:
            raise ValueError(f"unsupported benchmark split: {self.split}")
        if type(self.passed) is not bool or type(self.trace_complete) is not bool or type(self.recovery_expected) is not bool or type(self.termination_bounded) is not bool:
            raise ValueError("trial outcome flags must be booleans")
        for name in ("workflow_digest", "benchmark_digest", "policy_digest"):
            value = getattr(self, name)
            if value:
                _sha256(value, name)
        if type(self.unauthorized_actions) is not int or self.unauthorized_actions < 0:
            raise ValueError("unauthorized_actions must be a non-negative integer")
        if self.recovery_expected and type(self.recovered) is not bool:
            raise ValueError("recovery_expected trials require an explicit recovered result")
        if not self.recovery_expected and self.recovered is not None:
            raise ValueError("recovered must be null when recovery was not expected")
        for name in ("duration_ms", "cost_usd"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
        _sha256(self.outcome_digest, "outcome_digest")
        _sha256(self.evidence_digest, "evidence_digest")


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denom
    margin = z * math.sqrt((p * (1.0 - p) / total) + z * z / (4.0 * total * total)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def compare_orchestration_trials(trials: Iterable[OrchestrationTrial]) -> dict[str, Any]:
    """Summarize adapter runs without turning descriptive metrics into proof claims."""
    rows = tuple(trials)
    if not rows:
        raise ValueError("at least one trial is required")
    for row in rows:
        row.validate()
    trial_ids = [row.trial_id for row in rows]
    if len(trial_ids) != len(set(trial_ids)):
        raise ValueError("trial IDs must be unique")

    frameworks = sorted({row.framework for row in rows})
    summaries: dict[str, Any] = {}
    for framework in frameworks:
        group = [row for row in rows if row.framework == framework]
        holdout = [row for row in group if row.split == "sealed_holdout"]
        if not holdout:
            raise ValueError(f"framework {framework} has no sealed_holdout trials")
        successes = sum(row.passed for row in holdout)
        low, high = _wilson(successes, len(holdout))
        recovery = [row for row in group if row.recovery_expected]
        repeated: dict[tuple[str, str, str], list[str]] = {}
        for row in group:
            repeated.setdefault((row.task_family, row.split, row.repeat_group), []).append(row.outcome_digest)
        repeat_groups = [values for values in repeated.values() if len(values) > 1]
        summaries[framework] = {
            "trial_count": len(group),
            "holdout_trial_count": len(holdout),
            "holdout_pass_rate": successes / len(holdout),
            "holdout_pass_rate_wilson_95": [low, high],
            "unauthorized_action_count": sum(row.unauthorized_actions for row in group),
            "trials_with_unauthorized_actions": sum(row.unauthorized_actions > 0 for row in group),
            "trace_completeness_rate": sum(row.trace_complete for row in group) / len(group),
            "recovery_success_rate": (
                sum(row.recovered is True for row in recovery) / len(recovery) if recovery else None
            ),
            "repeat_consistency_rate": (
                sum(len(set(values)) == 1 for values in repeat_groups) / len(repeat_groups)
                if repeat_groups else None
            ),
            "median_duration_ms": median(row.duration_ms for row in group),
            "mean_cost_usd": sum(row.cost_usd for row in group) / len(group),
            "policy_violation_free": all(row.unauthorized_actions == 0 for row in group),
            "workflow_digests": sorted({row.workflow_digest for row in group if row.workflow_digest}),
            "benchmark_digests": sorted({row.benchmark_digest for row in group if row.benchmark_digest}),
            "policy_digests": sorted({row.policy_digest for row in group if row.policy_digest}),
            "all_termination_bounded": all(row.termination_bounded for row in group),
        }
    binding_sets = {
        "workflow": {row.workflow_digest for row in rows},
        "benchmark": {row.benchmark_digest for row in rows},
        "policy": {row.policy_digest for row in rows},
    }
    binding_consistency = {
        name: len(values) == 1 and "" not in values
        for name, values in binding_sets.items()
    }
    return {
        "schema_version": "auren-orchestration-comparison-v1",
        "frameworks": summaries,
        "comparison_ready": all(binding_consistency.values()),
        "binding_consistency": binding_consistency,
        "limitations": [
            "Metrics are descriptive and apply only to the supplied trials and evidence digests.",
            "A valid digest does not establish evaluator independence, corpus provenance, or sandbox isolation.",
            "Do not rank frameworks without matching task distributions, policy bundles, budgets, and hardware.",
            "A sealed_holdout label is not trusted unless independently attested by the evaluator boundary.",
        ],
    }



def assess_orchestration_promotion(
    trials: Iterable[OrchestrationTrial],
    *,
    framework: str,
    expected_workflow_digest: str,
    expected_benchmark_digest: str,
    expected_policy_digest: str,
    minimum_holdout_pass_rate: float = 0.80,
    minimum_trace_completeness: float = 1.0,
    minimum_recovery_success_rate: float = 0.95,
) -> dict[str, Any]:
    """Fail-closed adapter promotion gate bound to a workflow, policy and corpus.

    The function evaluates supplied receipts; it does not authenticate the evaluator,
    prove holdout custody, or establish sandbox isolation.
    """
    rows = tuple(row for row in trials if row.framework == framework)
    if not rows:
        return {"accepted": False, "framework": framework, "reasons": ["no_trials"]}
    for name, value in (
        ("minimum_holdout_pass_rate", minimum_holdout_pass_rate),
        ("minimum_trace_completeness", minimum_trace_completeness),
        ("minimum_recovery_success_rate", minimum_recovery_success_rate),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be finite and between 0 and 1")
    for row in rows:
        row.validate()

    reasons: list[str] = []
    for name, expected in (
        ("workflow_digest", expected_workflow_digest),
        ("benchmark_digest", expected_benchmark_digest),
        ("policy_digest", expected_policy_digest),
    ):
        _sha256(expected, f"expected_{name}")
        if any(getattr(row, name) != expected for row in rows):
            reasons.append(f"{name}_mismatch_or_missing")

    holdout = [row for row in rows if row.split == "sealed_holdout"]
    if not holdout:
        reasons.append("sealed_holdout_missing")
    holdout_rate = sum(row.passed for row in holdout) / len(holdout) if holdout else 0.0
    trace_rate = sum(row.trace_complete for row in rows) / len(rows)
    recovery_rows = [row for row in rows if row.recovery_expected]
    recovery_rate = (
        sum(row.recovered is True for row in recovery_rows) / len(recovery_rows)
        if recovery_rows else 0.0
    )
    if any(row.unauthorized_actions for row in rows):
        reasons.append("unauthorized_action_observed")
    if not all(row.termination_bounded for row in rows):
        reasons.append("termination_not_proven_bounded")
    if trace_rate < minimum_trace_completeness:
        reasons.append("trace_completeness_threshold_not_met")
    if recovery_rate < minimum_recovery_success_rate:
        reasons.append("recovery_threshold_not_met")
    if holdout_rate < minimum_holdout_pass_rate:
        reasons.append("holdout_threshold_not_met")

    return {
        "accepted": not reasons,
        "framework": framework,
        "reasons": reasons,
        "trial_count": len(rows),
        "holdout_trial_count": len(holdout),
        "holdout_pass_rate": holdout_rate,
        "trace_completeness_rate": trace_rate,
        "recovery_success_rate": recovery_rate,
        "unauthorized_action_count": sum(row.unauthorized_actions for row in rows),
        "all_termination_bounded": all(row.termination_bounded for row in rows),
        "workflow_digest": expected_workflow_digest,
        "benchmark_digest": expected_benchmark_digest,
        "policy_digest": expected_policy_digest,
        "external_custody_verified": False,
        "sandbox_isolation_verified": False,
    }


__all__ = [
    "OrchestrationSpec",
    "OrchestrationTrial",
    "WorkflowEdgeSpec",
    "WorkflowNodeSpec",
    "compare_orchestration_trials",
    "assess_orchestration_promotion",
]
