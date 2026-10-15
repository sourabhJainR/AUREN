"""Adaptive adapters connecting AUREN's 18-stage lifecycle to active host tools.

Providers remain injected ports: discovery is evidence-based and no external
provider, network access, deployment credential, or repository write is assumed.
Learning proposes candidates; promotion requires isolated evaluation and safety
evidence. External side effects are never blindly retried.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .agency_registry import rank as rank_agents
from .agent_capabilities import redact
from .vibe_coding import STAGE_ORDER, STAGE_SPECS, VibeStage, build_vibe_coding_graph

Adapter = Callable[[Mapping[str, Any]], Mapping[str, Any]]
_SCHEMA = "auren-vibe-adapter-learning-v1"
_SECRET_KEY = re.compile(r"(?i)(secret|token|password|credential|authorization|api[_-]?key|private[_-]?key)")
_STAGE_STATE_KEYS = frozenset({
    "intent", "task_intent", "task_id", "vibe_coding_workflow_id", "risk",
    "repository", "workspace_root", "project_root", "branch", "base_revision",
    "preview_deployment_required", "preview_deployment_rationale",
    "production_deployment_required", "production_deployment_rationale",
    "production_approval", "final_code_revision", "verified_code_revision",
    "environment", "requirements", "acceptance_criteria",
})


def _safe_payload(value: Any, key: str = "") -> Any:
    """Bound adapter context and strip common secret-bearing fields."""
    if key and _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(child_key): _safe_payload(child_value, str(child_key))
            for child_key, child_value in value.items()
            if not _SECRET_KEY.search(str(child_key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_payload(item) for item in value[:200]]
    if isinstance(value, str):
        return redact(value[:12000])
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:2000]
_STAGE_CAPABILITIES: dict[VibeStage, tuple[str, ...]] = {
    VibeStage.IDEA: ("agent", "product"),
    VibeStage.RESEARCH: ("research", "web_search"),
    VibeStage.DEFINE_USER: ("agent", "research"),
    VibeStage.PRD: ("agent", "product"),
    VibeStage.TECH_STACK: ("agent", "research"),
    VibeStage.ARCHITECTURE: ("agent", "repository"),
    VibeStage.DESIGN: ("design", "accessibility"),
    VibeStage.PROJECT_RULES: ("repository", "security"),
    VibeStage.TASK_BREAKDOWN: ("agent", "task_planning"),
    VibeStage.SETUP: ("repository", "environment"),
    VibeStage.DEVELOPMENT: ("agent", "repository"),
    VibeStage.TESTING: ("repository", "test_execution"),
    VibeStage.SECURITY_REVIEW: ("security", "repository"),
    VibeStage.CODE_REVIEW: ("independent_review", "repository"),
    VibeStage.PREVIEW_DEPLOYMENT: ("deployment", "preview"),
    VibeStage.QA_TESTING: ("browser", "test_execution"),
    VibeStage.PRODUCTION_DEPLOY: ("deployment", "release"),
    VibeStage.MONITORING: ("observability", "rollback"),
}
_SIDE_EFFECT_STAGES = frozenset({
    VibeStage.DEVELOPMENT, VibeStage.PREVIEW_DEPLOYMENT,
    VibeStage.PRODUCTION_DEPLOY,
})


class AdapterUnavailable(RuntimeError):
    """Raised before side effects when an adapter cannot serve a request."""


@dataclass(frozen=True)
class AdapterRegistration:
    adapter_id: str
    version: str
    stages: tuple[VibeStage, ...]
    capabilities: frozenset[str]
    invoke: Adapter
    risk: str = "low"
    evidence_quality: float = 0.5
    historical_success: float = 0.5
    estimated_cost: float = 0.5
    estimated_latency_ms: float = 500.0
    external_side_effects: bool = False

    def __post_init__(self) -> None:
        if not self.adapter_id.strip() or not self.version.strip():
            raise ValueError("adapter_id and version are required")
        if not self.stages or not callable(self.invoke):
            raise ValueError("adapter must serve at least one stage and have a callable")
        if self.risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("adapter risk must be low, medium, high, or critical")
        for name in ("evidence_quality", "historical_success"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if not math.isfinite(self.estimated_cost) or self.estimated_cost < 0:
            raise ValueError("estimated_cost must be finite and non-negative")
        if not math.isfinite(self.estimated_latency_ms) or self.estimated_latency_ms < 0:
            raise ValueError("estimated_latency_ms must be finite and non-negative")
        if self.external_side_effects and not set(self.stages).intersection(_SIDE_EFFECT_STAGES):
            raise ValueError("external side effects are only allowed on declared side-effect stages")


@dataclass
class AdapterStats:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    workflows: set[str] = field(default_factory=set)

    @property
    def success_rate(self) -> float:
        # Beta(2,2) smoothing prevents one lucky run from dominating selection.
        return (self.successes + 2) / (self.attempts + 4)


class VibeAdapterRegistry:
    """Capability-aware adapter routing with bounded, explainable adaptation."""

    def __init__(self, *, max_risk: str = "high", exploration: float = 0.08) -> None:
        if max_risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("invalid max_risk")
        if not math.isfinite(exploration) or not 0 <= exploration <= 0.2:
            raise ValueError("exploration must be between 0 and 0.2")
        self.max_risk = max_risk
        self.exploration = exploration
        self._risk_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        self._adapters: dict[str, AdapterRegistration] = {}
        self._stats: dict[str, AdapterStats] = {}
        self._evaluation_stats: dict[str, AdapterStats] = {}

    def register(self, registration: AdapterRegistration) -> None:
        if not isinstance(registration, AdapterRegistration):
            raise TypeError("registration must be AdapterRegistration")
        previous = self._adapters.get(registration.adapter_id)
        if previous and registration.version == previous.version and registration != previous:
            raise ValueError("same adapter version cannot change implementation metadata")
        self._adapters[registration.adapter_id] = registration
        self._stats.setdefault(registration.adapter_id, AdapterStats())
        self._evaluation_stats.setdefault(registration.adapter_id, AdapterStats())

    def discover(self, stage: VibeStage, capabilities: Sequence[str] | None = None) -> tuple[AdapterRegistration, ...]:
        required = set(capabilities or _STAGE_CAPABILITIES[stage])
        return tuple(sorted(
            (item for item in self._adapters.values()
             if stage in item.stages and required.issubset(item.capabilities)
             and self._risk_rank[item.risk] <= self._risk_rank[self.max_risk]),
            key=lambda item: item.adapter_id,
        ))

    def _score(self, item: AdapterRegistration) -> float:
        stats = self._stats[item.adapter_id]
        reliability = 0.65 * stats.success_rate + 0.35 * item.historical_success
        cost_penalty = min(item.estimated_cost, 10.0) / 50
        latency_penalty = min(item.estimated_latency_ms, 60000) / 600000
        return 0.55 * reliability + 0.35 * item.evidence_quality - cost_penalty - latency_penalty

    def select(self, stage: VibeStage, capabilities: Sequence[str] | None = None) -> AdapterRegistration:
        candidates = self.discover(stage, capabilities)
        if not candidates:
            raise AdapterUnavailable(
                f"no registered adapter for stage={stage.value}, "
                f"capabilities={list(capabilities or _STAGE_CAPABILITIES[stage])}"
            )
        return max(candidates, key=lambda item: (self._score(item), item.adapter_id))

    def invoke(self, stage: VibeStage, state: Mapping[str, Any], *,
               capabilities: Sequence[str] | None = None) -> Mapping[str, Any]:
        selected = self.select(stage, capabilities)
        stats = self._stats[selected.adapter_id]
        workflow_id = str(state.get("vibe_coding_workflow_id", state.get("task_id", "unknown")))
        stats.attempts += 1
        stats.workflows.add(workflow_id)
        # One call only. Do not replay uncertain deployment/repository side effects.
        request = {
            "schema_version": "auren-vibe-stage-request-v1",
            "stage": stage.value,
            "purpose": next(spec.purpose for spec in STAGE_SPECS if spec.stage == stage),
            "required_capabilities": list(capabilities or _STAGE_CAPABILITIES[stage]),
            "intent": str(state.get("intent", state.get("task_intent", ""))),
            "prior_stage_outputs": _safe_payload({
                prior.value: state[prior.value] for prior in STAGE_ORDER
                if prior.value in state and isinstance(state[prior.value], Mapping)
            }),
            "state": _safe_payload({
                key: value for key, value in state.items()
                if key in _STAGE_STATE_KEYS
                and (key != "production_approval" or stage == VibeStage.PRODUCTION_DEPLOY)
            }),
            "agent_candidates": rank_agents(
                str(state.get("intent", state.get("task_intent", "")) or ""),
                limit=5,
            ) if stage in {VibeStage.IDEA, VibeStage.DEFINE_USER, VibeStage.PRD,
                           VibeStage.TECH_STACK, VibeStage.ARCHITECTURE,
                           VibeStage.TASK_BREAKDOWN, VibeStage.DEVELOPMENT} else [],
            "adapter": {"id": selected.adapter_id, "version": selected.version},
        }
        try:
            output = selected.invoke(request)
            if not isinstance(output, Mapping):
                raise TypeError("adapter must return a structured mapping")
            result = dict(output)
            # Evidence records which real adapter executed the stage. It is metadata,
            # not proof of correctness; stage validators and independent gates still apply.
            evidence = result.get("evidence")
            if not isinstance(evidence, list):
                evidence = [evidence] if evidence else []
            result["evidence"] = list(evidence) + [{
                "kind": "adapter-execution",
                "adapter_id": selected.adapter_id,
                "adapter_version": selected.version,
                "stage": stage.value,
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }]
            result.setdefault("adapter_id", selected.adapter_id)
            result.setdefault("adapter_version", selected.version)
            stats.successes += 1
            return result
        except Exception:
            stats.failures += 1
            raise

    def record_evaluation(self, adapter_id: str, *, workflow_id: str,
                          passed: bool, evidence_refs: Sequence[str]) -> None:
        if adapter_id not in self._adapters:
            raise KeyError(f"unknown adapter: {adapter_id}")
        if not workflow_id.strip() or not evidence_refs or any(not str(ref).strip() for ref in evidence_refs):
            raise ValueError("evaluation needs workflow identity and evidence references")
        stats = self._evaluation_stats[adapter_id]
        stats.workflows.add(workflow_id)
        stats.attempts += 1
        if passed:
            stats.successes += 1
        else:
            stats.failures += 1

    def evolution_candidates(self) -> list[dict[str, Any]]:
        """Return proposals only after enough independently evaluated workflow evidence."""
        proposals = []
        for adapter_id, stats in sorted(self._evaluation_stats.items()):
            if stats.attempts < 5 or len(stats.workflows) < 3:
                continue
            rate = stats.success_rate
            if rate < 0.8:
                continue
            registration = self._adapters[adapter_id]
            payload = {
                "schema_version": "auren-vibe-adapter-candidate-v1",
                "adapter_id": adapter_id,
                "adapter_version": registration.version,
                "observations": stats.attempts,
                "distinct_workflows": len(stats.workflows),
                "smoothed_success_rate": rate,
                "evidence_quality": registration.evidence_quality,
                "required_next_gates": ["isolated_regression", "independent_review", "security_review", "holdout_transfer"],
                "status": "candidate_only",
            }
            payload["digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            proposals.append(payload)
        return proposals

    def snapshot(self) -> dict[str, Any]:
        stats = {key: {
            "attempts": value.attempts, "successes": value.successes,
            "failures": value.failures, "workflows": sorted(value.workflows),
        } for key, value in sorted(self._stats.items())}
        evaluations = {key: {
            "attempts": value.attempts, "successes": value.successes,
            "failures": value.failures, "workflows": sorted(value.workflows),
        } for key, value in sorted(self._evaluation_stats.items())}
        payload = {"schema_version": _SCHEMA, "stats": stats, "evaluations": evaluations}
        payload["digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return payload

    def restore_stats(self, snapshot: Mapping[str, Any]) -> None:
        if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != _SCHEMA:
            raise ValueError("unsupported adapter-learning snapshot")
        payload = {key: value for key, value in snapshot.items() if key != "digest"}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if snapshot.get("digest") != digest:
            raise ValueError("adapter-learning snapshot digest mismatch")
        for section, destination in (("stats", self._stats), ("evaluations", self._evaluation_stats)):
            incoming = snapshot.get(section)
            if not isinstance(incoming, Mapping):
                raise ValueError(f"adapter-learning {section} must be a mapping")
            for adapter_id, raw in incoming.items():
                if adapter_id not in self._adapters or not isinstance(raw, Mapping):
                    raise ValueError("learning snapshot references an unregistered adapter")
                attempts, successes, failures = (raw.get(key) for key in ("attempts", "successes", "failures"))
                workflows = raw.get("workflows")
                if any(type(value) is not int or value < 0 for value in (attempts, successes, failures)):
                    raise ValueError("learning counters must be non-negative integers")
                if successes + failures > attempts or not isinstance(workflows, list) or any(not isinstance(x, str) for x in workflows):
                    raise ValueError("invalid adapter learning counters or workflow list")
                destination[adapter_id] = AdapterStats(attempts, successes, failures, set(workflows))


def build_integrated_vibe_coding_graph(
    adapters: VibeAdapterRegistry,
    *,
    checkpoint_writer: Callable[[Mapping[str, Any]], None] | None = None,
    stage_capabilities: Mapping[VibeStage | str, Sequence[str]] | None = None,
):
    """Bind all 18 stages to real registered adapters and AUREN's evidence gates.

    This fails fast if even one stage lacks a capability-compatible adapter. The
    host injects real agents, research/design/repository tools, and deployment
    ports as registrations; no fake success handler is installed.
    """
    overrides: dict[VibeStage, Sequence[str]] = {}
    for key, value in (stage_capabilities or {}).items():
        stage = key if isinstance(key, VibeStage) else VibeStage(str(key))
        if not value or any(not isinstance(cap, str) or not cap.strip() for cap in value):
            raise ValueError(f"invalid capability override for {stage.value}")
        overrides[stage] = tuple(value)
    missing = [
        f"{stage.value}({','.join(overrides.get(stage, _STAGE_CAPABILITIES[stage]))})"
        for stage in STAGE_ORDER
        if not adapters.discover(stage, overrides.get(stage))
    ]
    if missing:
        raise AdapterUnavailable("integrated workflow is missing stage adapters: " + "; ".join(missing))
    handlers = {
        stage: (lambda state, _stage=stage: adapters.invoke(
            _stage, state, capabilities=overrides.get(_stage)
        ))
        for stage in STAGE_ORDER
    }
    return build_vibe_coding_graph(handlers, checkpoint_writer=checkpoint_writer)


def default_stage_capabilities() -> dict[str, list[str]]:
    """Expose the explicit capability contract for host/provider configuration."""
    return {stage.value: list(_STAGE_CAPABILITIES[stage]) for stage in STAGE_ORDER}


__all__ = [
    "AdapterRegistration", "AdapterStats", "AdapterUnavailable",
    "VibeAdapterRegistry", "build_integrated_vibe_coding_graph",
    "default_stage_capabilities",
]
