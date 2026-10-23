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
import os
import re
import time
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .agency_registry import rank as rank_agents
from .agent_capabilities import redact
from .vibe_coding import STAGE_ORDER, STAGE_SPECS, VibeStage, build_vibe_coding_graph, validate_stage_output
from .provider_operation_lifecycle import ProviderOperationLifecycle
from .side_effect_ledger import DurableSideEffectLedger

Adapter = Callable[[Mapping[str, Any]], Mapping[str, Any]]
_SCHEMA = "auren-vibe-adapter-learning-v3"
_SECRET_KEY = re.compile(r"(?i)(secret|token|password|credential|authorization|api[_-]?key|private[_-]?key)")
_STAGE_STATE_KEYS = frozenset({
    "intent", "task_intent", "task_id", "vibe_coding_workflow_id", "risk",
    "repository", "workspace_root", "project_root", "branch", "base_revision",
    "preview_deployment_required", "preview_deployment_rationale",
    "production_deployment_required", "production_deployment_rationale",
    "production_approval", "final_code_revision", "verified_code_revision",
    "environment", "requirements", "acceptance_criteria",
    "browser_qa_required", "browser_qa_rationale", "adapter_config_digest",
    "engineering_design",
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
_POST_AGENT_TOOL_STAGES = frozenset({
    VibeStage.DEVELOPMENT, VibeStage.TESTING, VibeStage.SECURITY_REVIEW,
    VibeStage.CODE_REVIEW, VibeStage.PREVIEW_DEPLOYMENT, VibeStage.QA_TESTING,
    VibeStage.PRODUCTION_DEPLOY, VibeStage.MONITORING,
})
_TOOL_AUTHORITY: dict[VibeStage, dict[str, tuple[str, ...]]] = {
    VibeStage.RESEARCH: {"research": ("market_evidence", "competitors")},
    VibeStage.PRD: {"product": ("requirements", "acceptance_criteria")},
    VibeStage.DESIGN: {"design": ("design_artifacts",)},
    VibeStage.TASK_BREAKDOWN: {"task_planning": ("tasks",)},
    VibeStage.SETUP: {"repository": ("repository", "environment")},
    VibeStage.DEVELOPMENT: {"repository": ("artifacts",)},
    VibeStage.TESTING: {"test_execution": ("tests_run", "status")},
    VibeStage.SECURITY_REVIEW: {"security": ("findings", "status", "critical_findings")},
    VibeStage.CODE_REVIEW: {"independent_review": ("reviewer", "review_summary", "status", "blocking_findings")},
    VibeStage.PREVIEW_DEPLOYMENT: {"deployment": ("deployment_receipt",)},
    VibeStage.QA_TESTING: {"test_execution": ("qa_results", "user_feedback", "status")},
    VibeStage.PRODUCTION_DEPLOY: {"release": ("release_receipt", "deployed_revision", "reviewed_revision", "approval")},
    VibeStage.MONITORING: {"observability": ("signals", "alerts", "rollback_thresholds")},
}


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
    lookup_operation: Callable[[str, str], Mapping[str, Any]] | None = None
    supports_provider_idempotency: bool = False
    lookup_not_found_is_authoritative: bool = False

    def __post_init__(self) -> None:
        if not self.adapter_id.strip() or not self.version.strip():
            raise ValueError("adapter_id and version are required")
        if not self.stages or not callable(self.invoke):
            raise ValueError("adapter must serve at least one stage and have a callable")
        if self.lookup_operation is not None and not callable(self.lookup_operation):
            raise ValueError("lookup_operation must be callable when configured")
        if not isinstance(self.supports_provider_idempotency, bool):
            raise ValueError("supports_provider_idempotency must be boolean")
        if not isinstance(self.lookup_not_found_is_authoritative, bool):
            raise ValueError("lookup_not_found_is_authoritative must be boolean")
        if self.lookup_not_found_is_authoritative and self.lookup_operation is None:
            raise ValueError("authoritative lookup requires a configured provider lookup operation")
        if self.risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("adapter risk must be low, medium, high, or critical")
        risk_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        inherent_risk = max(
            risk_rank[next(spec.risk for spec in STAGE_SPECS if spec.stage == stage)]
            for stage in self.stages
        )
        if risk_rank[self.risk] < inherent_risk:
            raise ValueError("adapter risk cannot understate the inherent risk of a served stage")
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
    evidence_refs: set[str] = field(default_factory=set)

    @property
    def success_rate(self) -> float:
        # Beta(2,2) smoothing prevents one lucky run from dominating selection.
        return (self.successes + 2) / (self.attempts + 4)


class VibeAdapterRegistry:
    """Capability-aware adapter routing with bounded, explainable adaptation."""

    def __init__(
        self, *, max_risk: str = "high", exploration: float = 0.08,
        learning_store_path: str | Path | None = None,
        side_effect_ledger: DurableSideEffectLedger | None = None,
        side_effect_event_recorder: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
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
        if side_effect_ledger is not None and not isinstance(side_effect_ledger, DurableSideEffectLedger):
            raise TypeError("side_effect_ledger must be the canonical DurableSideEffectLedger")
        if side_effect_event_recorder is not None and not callable(side_effect_event_recorder):
            raise TypeError("side_effect_event_recorder must be callable")
        self.side_effect_lifecycle = (
            ProviderOperationLifecycle(
                side_effect_ledger, record_event=side_effect_event_recorder,
            ) if side_effect_ledger is not None else None
        )
        self.learning_store_path = Path(learning_store_path).expanduser() if learning_store_path else None
        self._learning_loaded = False

    def attach_learning_store(self, path: str | Path, *, load_existing: bool = True) -> None:
        """Enable cross-session receipts after all adapter identities have been registered."""
        self.learning_store_path = Path(path).expanduser()
        if load_existing and self.learning_store_path.exists():
            self.load_snapshot(self.learning_store_path)
        self._learning_loaded = True
        self._persist_learning()

    def _persist_learning(self) -> None:
        if self.learning_store_path is not None and self._learning_loaded:
            self.save_snapshot(self.learning_store_path)

    @property
    def configuration_digest(self) -> str:
        rows = [
            {
                "adapter_id": item.adapter_id,
                "version": item.version,
                "stages": sorted(stage.value for stage in item.stages),
                "capabilities": sorted(item.capabilities),
                "risk": item.risk,
                "evidence_quality": item.evidence_quality,
                "historical_success": item.historical_success,
                "estimated_cost": item.estimated_cost,
                "estimated_latency_ms": item.estimated_latency_ms,
                "external_side_effects": item.external_side_effects,
                "lookup_operation_configured": item.lookup_operation is not None,
                "supports_provider_idempotency": item.supports_provider_idempotency,
                "lookup_not_found_is_authoritative": item.lookup_not_found_is_authoritative,
            }
            for item in sorted(self._adapters.values(), key=lambda adapter: adapter.adapter_id)
        ]
        payload = {"max_risk": self.max_risk, "exploration": self.exploration, "adapters": rows}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

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
        exploration_bonus = self.exploration / math.sqrt(stats.attempts + 1)
        return 0.55 * reliability + 0.35 * item.evidence_quality - cost_penalty - latency_penalty + exploration_bonus

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
        workflow_id = str(state.get("vibe_coding_workflow_id", state.get("task_id", ""))).strip()
        if selected.external_side_effects:
            if self.side_effect_lifecycle is None:
                raise AdapterUnavailable(
                    f"stage {stage.value} uses external side effects but has no durable side-effect ledger"
                )
            if not workflow_id or workflow_id == "unknown":
                raise AdapterUnavailable(
                    f"stage {stage.value} requires a stable workflow_id before external side effects"
                )
            operation_key = (
                f"vibe-stage:{workflow_id}:{stage.value}:"
                f"{selected.adapter_id}:{selected.version}"
            )
            existing_effect = self.side_effect_lifecycle.ledger.get(operation_key)
            # Completed receipts are safe to replay without a remote lookup.
            # A new write or authorized retry needs a provider-native lookup port
            # so an ambiguous remote outcome can be resolved without redispatch.
            if (
                stage in {VibeStage.PREVIEW_DEPLOYMENT, VibeStage.PRODUCTION_DEPLOY}
                and (
                    selected.lookup_operation is None
                    or not selected.supports_provider_idempotency
                    or not selected.lookup_not_found_is_authoritative
                )
                and (existing_effect is None or existing_effect["state"] == "retry_authorized")
            ):
                raise AdapterUnavailable(
                    f"stage {stage.value} requires provider-native operation lookup plus idempotency support and authoritative lookup semantics before deployment dispatch"
                )
        stats = self._stats[selected.adapter_id]
        stats.attempts += 1
        stats.workflows.add(workflow_id)
        # Persist before external work, so a crash cannot erase a possibly side-effecting attempt.
        self._persist_learning()
        # One call only. Do not replay uncertain deployment/repository side effects.
        request = {
            "schema_version": "auren-vibe-stage-request-v1",
            "stage": stage.value,
            "purpose": next(spec.purpose for spec in STAGE_SPECS if spec.stage == stage),
            "required_capabilities": list(capabilities or _STAGE_CAPABILITIES[stage]),
            "intent": _safe_payload(str(state.get("intent", state.get("task_intent", "")))),
            "prior_stage_outputs": _safe_payload({
                prior.value: state[prior.value] for prior in STAGE_ORDER
                if prior.value in state and isinstance(state[prior.value], Mapping)
            }),
            "state": _safe_payload({
                key: value for key, value in state.items()
                if key in _STAGE_STATE_KEYS
                and (key != "production_approval" or stage == VibeStage.PRODUCTION_DEPLOY)
            }),
            "agent_candidates": _safe_payload(rank_agents(
                str(state.get("intent", state.get("task_intent", "")) or ""),
                limit=5,
            )) if stage in {VibeStage.IDEA, VibeStage.DEFINE_USER, VibeStage.PRD,
                            VibeStage.TECH_STACK, VibeStage.ARCHITECTURE,
                            VibeStage.TASK_BREAKDOWN, VibeStage.DEVELOPMENT} else [],
            "adapter": {"id": selected.adapter_id, "version": selected.version},
        }
        try:
            if selected.external_side_effects:
                assert self.side_effect_lifecycle is not None
                operation_key = (
                    f"vibe-stage:{workflow_id}:{stage.value}:"
                    f"{selected.adapter_id}:{selected.version}"
                )

                def dispatch_stage(intent: Mapping[str, Any], operation_id: str, _key: str) -> Mapping[str, Any]:
                    dispatch_request = dict(intent)
                    dispatch_request["provider_operation"] = {
                        "operation_id": operation_id,
                        "idempotency_key": _key,
                        "provider": selected.adapter_id,
                        "adapter_version": selected.version,
                    }
                    raw = selected.invoke(dispatch_request)
                    if not isinstance(raw, Mapping):
                        raise TypeError("adapter must return a structured mapping")
                    # AUREN's operation ID is not proof of a provider-side operation.
                    # The adapter must return the provider's own durable operation ID.
                    remote_operation_id = raw.get("provider_operation_id")
                    stage_result = {key: value for key, value in raw.items() if key != "provider_operation_id"}
                    valid, _reason = validate_stage_output(stage, stage_result)
                    if not valid:
                        invalid_receipt: dict[str, Any] = {
                            "status": "invalid", "result": dict(stage_result),
                        }
                        if remote_operation_id is not None:
                            invalid_receipt["provider_operation_id"] = remote_operation_id
                        return invalid_receipt
                    # Normalize stage success only after its output contract passes.
                    # Missing remote IDs are intentionally left missing so the
                    # lifecycle blocks completion and requires reconciliation.
                    receipt = {"status": "completed", "result": dict(stage_result)}
                    if remote_operation_id is not None:
                        receipt["provider_operation_id"] = remote_operation_id
                    return receipt

                durable_receipt = self.side_effect_lifecycle.execute(
                    operation_key, selected.adapter_id, request, dispatch_stage,
                    lookup=selected.lookup_operation,
                )
                output = durable_receipt.get("result")
                if not isinstance(output, Mapping):
                    raise AdapterUnavailable("durable side-effect receipt lacks a structured stage result")
            else:
                output = selected.invoke(request)
            if not isinstance(output, Mapping):
                raise TypeError("adapter must return a structured mapping")
            sanitized_output = _safe_payload(dict(output))
            if not isinstance(sanitized_output, Mapping):
                raise TypeError("adapter output must sanitize to a structured mapping")
            result = dict(sanitized_output)
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
            valid, reason = validate_stage_output(stage, result)
            if not valid:
                raise ValueError(f"{stage.value} adapter output failed its stage contract: {reason}")
            stats.successes += 1
            self._persist_learning()
            return result
        except Exception:
            stats.failures += 1
            self._persist_learning()
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
        stats.evidence_refs.update(str(ref).strip() for ref in evidence_refs)
        if passed:
            stats.successes += 1
        else:
            stats.failures += 1
        self._persist_learning()

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
                "evidence_refs": sorted(stats.evidence_refs)[:100],
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
            "evidence_refs": sorted(value.evidence_refs),
        } for key, value in sorted(self._stats.items())}
        evaluations = {key: {
            "attempts": value.attempts, "successes": value.successes,
            "failures": value.failures, "workflows": sorted(value.workflows),
            "evidence_refs": sorted(value.evidence_refs),
        } for key, value in sorted(self._evaluation_stats.items())}
        payload = {
            "schema_version": _SCHEMA,
            "adapter_config_digest": self.configuration_digest,
            "stats": stats,
            "evaluations": evaluations,
        }
        payload["digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return payload

    def restore_stats(self, snapshot: Mapping[str, Any]) -> None:
        if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != _SCHEMA:
            raise ValueError("unsupported adapter-learning snapshot")
        payload = {key: value for key, value in snapshot.items() if key != "digest"}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if snapshot.get("digest") != digest:
            raise ValueError("adapter-learning snapshot digest mismatch")
        if snapshot.get("adapter_config_digest") != self.configuration_digest:
            raise ValueError("adapter-learning snapshot adapter configuration mismatch")
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
                evidence_refs = raw.get("evidence_refs", [])
                if not isinstance(evidence_refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs):
                    raise ValueError("adapter evidence references must be non-empty strings")
                destination[adapter_id] = AdapterStats(
                    attempts, successes, failures, set(workflows), set(evidence_refs)
                )

    def save_snapshot(self, path: str | Path) -> Path:
        """Atomically persist a learning receipt with private file permissions."""
        untrusted_target = Path(path).expanduser()
        if untrusted_target.is_symlink():
            raise ValueError("adapter-learning snapshot target must not be a symlink")
        target = untrusted_target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(target.parent, 0o700)
        payload = json.dumps(self.snapshot(), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, target)
            if os.name != "nt":
                os.chmod(target, 0o600)
            try:
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return target

    def load_snapshot(self, path: str | Path) -> None:
        """Load a validated learning receipt; symlinks and broad POSIX modes fail closed."""
        source = Path(path).expanduser()
        if source.is_symlink():
            raise ValueError("adapter-learning snapshot must not be a symlink")
        if os.name != "nt" and source.exists() and source.stat().st_mode & 0o077:
            raise PermissionError("adapter-learning snapshot permissions must be private (0600)")
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("adapter-learning snapshot must be a mapping")
        self.restore_stats(raw)
        self._learning_loaded = True


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



def build_host_adapter_registry(
    active_agent: Adapter,
    tools: Mapping[str, Adapter],
    *,
    version: str = "host-registered",
    max_risk: str = "critical",
    risk_by_stage: Mapping[VibeStage | str, str] | None = None,
    side_effect_ledger: DurableSideEffectLedger | None = None,
    side_effect_event_recorder: Callable[[Mapping[str, Any]], None] | None = None,
    lookup_operations: Mapping[VibeStage | str, Callable[[str, str], Mapping[str, Any]]] | None = None,
    provider_capabilities: Mapping[VibeStage | str, Mapping[str, bool]] | None = None,
) -> VibeAdapterRegistry:
    """Wire one active-agent dispatcher plus concrete capability tools into all stages.

    The active_agent callback should route to AUREN's live GraphAgentTeam/provider
    runtime. Each tools entry is a real, permission-scoped host adapter keyed by
    capability (research, web_search, design, repository, deployment, etc.). Tool
    receipts override agent-generated claims for the same fields; tool evidence is
    retained. Missing capability adapters fail fast before execution. Stages that
    can write externally require side_effect_ledger; without it, invocation is
    blocked before the host adapter runs.
    """
    if not callable(active_agent):
        raise TypeError("active_agent must be a callable host agent dispatcher")
    if not isinstance(tools, Mapping):
        raise TypeError("tools must map capability names to callable adapters")
    invalid = [name for name, callback in tools.items()
               if not isinstance(name, str) or not name.strip() or not callable(callback)]
    if invalid:
        raise ValueError("tool adapter names must be non-empty and values callable")
    risk_overrides: dict[VibeStage, str] = {}
    for key, value in (risk_by_stage or {}).items():
        stage = key if isinstance(key, VibeStage) else VibeStage(str(key))
        if value not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"invalid adapter risk for {stage.value}")
        inherent_risk = next(spec.risk for spec in STAGE_SPECS if spec.stage == stage)
        risk_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        if risk_rank[value] < risk_rank[inherent_risk]:
            raise ValueError(f"adapter risk for {stage.value} cannot understate its stage risk ({inherent_risk})")
        risk_overrides[stage] = value

    required_tools = sorted({
        capability
        for stage in STAGE_ORDER
        for capability in _STAGE_CAPABILITIES[stage]
        if capability != "agent"
    })
    missing = [capability for capability in required_tools if capability not in tools]
    if missing:
        raise AdapterUnavailable("host integration missing capability tools: " + ", ".join(missing))

    lookup_by_stage: dict[VibeStage, Callable[[str, str], Mapping[str, Any]]] = {}
    for key, lookup in (lookup_operations or {}).items():
        try:
            stage = key if isinstance(key, VibeStage) else VibeStage(str(key))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid lookup stage: {key!r}") from exc
        if stage not in _SIDE_EFFECT_STAGES:
            raise ValueError(f"provider lookup is only configurable for side-effect stages: {stage.value}")
        if not callable(lookup):
            raise ValueError(f"lookup operation for {stage.value} must be callable")
        lookup_by_stage[stage] = lookup

    provider_capabilities_by_stage: dict[VibeStage, dict[str, bool]] = {}
    for key, capability_values in (provider_capabilities or {}).items():
        try:
            stage = key if isinstance(key, VibeStage) else VibeStage(str(key))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid provider-capability stage: {key!r}") from exc
        if stage not in {VibeStage.PREVIEW_DEPLOYMENT, VibeStage.PRODUCTION_DEPLOY}:
            raise ValueError(f"provider capabilities are only configurable for deployment stages: {stage.value}")
        if not isinstance(capability_values, Mapping):
            raise ValueError(f"provider capabilities for {stage.value} must be a mapping")
        unknown = set(capability_values) - {"supports_provider_idempotency", "lookup_not_found_is_authoritative"}
        if unknown:
            raise ValueError(f"unknown provider capabilities for {stage.value}: {sorted(unknown)}")
        values = {
            "supports_provider_idempotency": capability_values.get("supports_provider_idempotency", False),
            "lookup_not_found_is_authoritative": capability_values.get("lookup_not_found_is_authoritative", False),
        }
        if any(not isinstance(value, bool) for value in values.values()):
            raise ValueError(f"provider capabilities for {stage.value} must be booleans")
        if values["lookup_not_found_is_authoritative"] and stage not in lookup_by_stage:
            raise ValueError(f"authoritative lookup for {stage.value} requires a lookup operation")
        provider_capabilities_by_stage[stage] = values

    registry = VibeAdapterRegistry(
        max_risk=max_risk,
        side_effect_ledger=side_effect_ledger,
        side_effect_event_recorder=side_effect_event_recorder,
    )
    for stage in STAGE_ORDER:
        capabilities = tuple(_STAGE_CAPABILITIES[stage])
        def composite(request: Mapping[str, Any], *, _stage: VibeStage = stage,
                      _capabilities: tuple[str, ...] = capabilities) -> Mapping[str, Any]:
            tool_results: dict[str, Mapping[str, Any]] = {}
            tool_evidence: list[Any] = []
            agent_output: dict[str, Any] = {}
            agent_first = _stage in _POST_AGENT_TOOL_STAGES

            def invoke_tools(stage_output: Mapping[str, Any] | None = None) -> None:
                for capability in _capabilities:
                    if capability == "agent":
                        continue
                    tool_request = dict(request)
                    tool_request["capability"] = capability
                    if stage_output is not None:
                        tool_request["stage_output"] = _safe_payload(dict(stage_output))
                    tool_request["tool_results"] = dict(tool_results)
                    tool_result = tools[capability](tool_request)
                    if not isinstance(tool_result, Mapping):
                        raise TypeError(f"tool adapter {capability} must return a mapping")
                    tool_results[capability] = dict(tool_result)
                    evidence = tool_result.get("evidence", [])
                    if isinstance(evidence, list):
                        tool_evidence.extend(evidence)
                    elif evidence:
                        tool_evidence.append(evidence)

            if agent_first:
                if "agent" in _capabilities:
                    agent_output = dict(active_agent(dict(request, tool_results={})))
                invoke_tools(agent_output)
            else:
                invoke_tools()
                if "agent" in _capabilities:
                    agent_request = dict(request)
                    agent_request["tool_results"] = tool_results
                    raw_agent_output = active_agent(agent_request)
                    if not isinstance(raw_agent_output, Mapping):
                        raise TypeError("active agent dispatcher must return a structured mapping")
                    agent_output = dict(raw_agent_output)

            merged = dict(agent_output)
            evidence = merged.get("evidence", [])
            evidence_items = list(evidence) if isinstance(evidence, list) else ([evidence] if evidence else [])
            if _stage == VibeStage.RESEARCH:
                research_evidence = tool_results.get("research", {}).get("evidence", [])
                source_backed = isinstance(research_evidence, list) and any(
                    isinstance(item, Mapping)
                    and str(item.get("locator", "")).strip()
                    and str(item.get("source", "")).strip().lower() not in {"", "model", "llm", "assistant", "generated", "self"}
                    for item in research_evidence
                )
                if not source_backed:
                    raise AdapterUnavailable("research stage requires source-backed evidence from the research tool")
            # Consequential receipts must identify the environment and health outcome;
            # a model-generated description is never sufficient deployment evidence.
            if _stage == VibeStage.PREVIEW_DEPLOYMENT:
                deployment = tool_results.get("deployment", {})
                preview = tool_results.get("preview", {})
                if deployment.get("environment") not in {"preview", "staging", "isolated"}:
                    raise AdapterUnavailable("preview deployment receipt must identify a non-production environment")
                preview_url = preview.get("preview_url") or preview.get("url")
                if not isinstance(preview_url, str) or not preview_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
                    raise AdapterUnavailable("preview tool must return a valid HTTPS or loopback preview_url")
                if preview.get("health_status") != "passed":
                    raise AdapterUnavailable("preview health check must explicitly report health_status='passed'")
            if _stage == VibeStage.PRODUCTION_DEPLOY:
                release = tool_results.get("release", {})
                if release.get("environment") != "production":
                    raise AdapterUnavailable("release receipt must identify the production environment")
                if dict(release.get("approval", {})) != dict(request.get("state", {}).get("production_approval", {})):
                    raise AdapterUnavailable("release tool approval does not match the explicit workflow authorization")
            # Tool-owned fields cannot be invented by the agent; tool results must
            # be from the post-mutation state for testing, review, and deployment.
            for capability, fields in _TOOL_AUTHORITY.get(_stage, {}).items():
                tool_result = tool_results.get(capability, {})
                missing_fields = [field for field in fields if field not in tool_result or tool_result[field] is None]
                if missing_fields:
                    raise AdapterUnavailable(
                        f"{_stage.value} requires {capability} tool receipts for: {', '.join(missing_fields)}"
                    )
            for capability in _capabilities:
                if capability == "agent":
                    continue
                tool_result = tool_results[capability]
                for key, value in tool_result.items():
                    if key != "evidence":
                        merged[key] = value
            merged["evidence"] = evidence_items + tool_evidence
            merged.setdefault("stage", _stage.value)
            return merged

        registry.register(AdapterRegistration(
            adapter_id=f"host-integrated:{stage.value}",
            version=version,
            stages=(stage,),
            capabilities=frozenset(set(capabilities) | {"agent"}),
            invoke=composite,
            risk=risk_overrides.get(
                stage, next(spec.risk for spec in STAGE_SPECS if spec.stage == stage)
            ),
            evidence_quality=0.65,
            historical_success=0.5,
            external_side_effects=stage in _SIDE_EFFECT_STAGES,
            lookup_operation=lookup_by_stage.get(stage),
            supports_provider_idempotency=provider_capabilities_by_stage.get(stage, {}).get("supports_provider_idempotency", False),
            lookup_not_found_is_authoritative=provider_capabilities_by_stage.get(stage, {}).get("lookup_not_found_is_authoritative", False),
        ))
    return registry


def default_stage_capabilities() -> dict[str, list[str]]:
    """Expose the explicit capability contract for host/provider configuration."""
    return {stage.value: list(_STAGE_CAPABILITIES[stage]) for stage in STAGE_ORDER}


__all__ = [
    "AdapterRegistration", "AdapterStats", "AdapterUnavailable",
    "VibeAdapterRegistry", "build_host_adapter_registry", "build_integrated_vibe_coding_graph",
    "default_stage_capabilities",
]
