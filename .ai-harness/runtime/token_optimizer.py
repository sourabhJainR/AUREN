#!/usr/bin/env python3
"""Deterministic token-budget controls for provider-neutral coding runs.

The optimizer follows three rules: send only relevant context, use the
smallest model tier that can safely perform the task, and replace repeatable
AI work with deterministic scripts. It never mutates source code or commits
changes.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import re
from typing import Iterable


@dataclass(frozen=True)
class ContextSlice:
    text: str
    score: float
    reason: str


@dataclass(frozen=True)
class RouteDecision:
    tier: str
    reason: str
    estimated_input_tokens: int
    estimated_cost_units: float | None
    provider: str = ""
    model: str = ""
    estimated_output_tokens: int = 0
    estimated_latency_ms: int = 0


@dataclass(frozen=True)
class ModelOption:
    provider: str
    model: str
    tier: str
    input_cost_per_million: float
    output_cost_per_million: float
    max_context_tokens: int
    capabilities: tuple[str, ...]
    privacy_levels: tuple[str, ...]
    availability_verified: bool
    estimated_latency_ms: int = 0

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("provider and model are required")
        if self.tier not in {"low", "standard", "high"}:
            raise ValueError("unsupported model tier")
        if self.input_cost_per_million < 0 or self.output_cost_per_million < 0:
            raise ValueError("model prices cannot be negative")
        if self.max_context_tokens < 1 or self.estimated_latency_ms < 0:
            raise ValueError("invalid model capacity or latency")


@dataclass(frozen=True)
class OptimizationReport:
    original_chars: int
    selected_chars: int
    estimated_input_tokens: int
    compression_ratio: float
    selected_sections: int
    route: RouteDecision
    script_candidate: bool
    estimated_output_tokens: int = 0
    estimated_cost_units: float | None = None
    selected_model: str | None = None
    cache_key: str | None = None


def estimate_tokens(text: str) -> int:
    return max(0, (len(text) + 3) // 4)


def _terms(text: str) -> set[str]:
    return {x.lower() for x in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{2,}", text)}


def select_relevant_context(query: str, sections: Iterable[str], budget_chars: int, *, required_sections: Iterable[str] = ()) -> list[ContextSlice]:
    """Rank sections by lexical overlap and pack the highest-value evidence.

    The query itself is never returned; only supplied repository evidence is
    selected. This is intentionally deterministic so routing is auditable.
    """
    q = _terms(query)
    supplied = [str(section).strip() for section in sections if str(section).strip()]
    required = list(dict.fromkeys(str(section).strip() for section in required_sections if str(section).strip()))
    missing_required = [item for item in required if item not in supplied]
    if missing_required:
        raise ValueError("required context section is missing from supplied evidence")
    required_chars = sum(len(item) + 1 for item in required)
    if required_chars > max(0, int(budget_chars)):
        raise ValueError("required policy/acceptance context exceeds the context budget")
    ranked: list[ContextSlice] = [ContextSlice(item, 1.0, "required-preserved") for item in required]
    for section in supplied:
        text = str(section).strip()
        if not text:
            continue
        terms = _terms(text)
        overlap = len(q & terms)
        score = overlap / max(1, len(q))
        if overlap == 0 and q:
            # Keep very small structural headers only when no better evidence
            # exists; unrelated large blobs should not consume the budget.
            score = 0.01 if len(text) <= 240 else 0.0
        ranked.append(ContextSlice(text, score, "query-overlap" if overlap else "structural-fallback"))
    ranked.sort(key=lambda x: (-x.score, len(x.text), hashlib.sha256(x.text.encode()).hexdigest()))
    selected: list[ContextSlice] = list(ranked[:len(required)])
    selected_texts = {item.text for item in selected}
    used = required_chars
    for item in ranked:
        if item.text in selected_texts or item.score <= 0:
            continue
        if used + len(item.text) + 1 > max(0, int(budget_chars)):
            continue
        selected.append(item)
        used += len(item.text) + 1
    return selected


def choose_model_tier(*, task: str, risk: str = "normal", uncertainty: str = "known", verification_failures: int = 0) -> RouteDecision:
    """Apply the Minimum Viable Model rule before escalation."""
    normalized_risk = risk.lower()
    words = _terms(task)
    hard = {"architecture", "migration", "security", "incident", "concurrency", "distributed", "production"}
    simple = {"format", "rename", "summarize", "classification", "documentation", "typo", "changelog"}
    if normalized_risk in {"critical", "high"} or uncertainty == "unknown" or verification_failures >= 2:
        tier, reason = "high", "risk-or-uncertainty-requires-stronger-reasoning"
    elif words & hard:
        tier, reason = "high", "task-signals-complex-reasoning"
    elif words & simple or len(words) <= 4:
        tier, reason = "low", "minimum-viable-model-for-simple-task"
    else:
        tier, reason = "standard", "default-model-for-normal-implementation"
    return RouteDecision(tier, reason, 0, 0.0)


def should_use_script(task: str, repeat_count: int = 0) -> bool:
    """Recommend deterministic automation for repeatable mechanical work."""
    mechanical = ("format", "lint", "generate", "validate", "check", "sync", "convert", "inventory")
    return repeat_count >= 2 or any(word in task.lower() for word in mechanical)


def optimize(query: str, sections: Iterable[str], budget_chars: int, *, risk: str = "normal", uncertainty: str = "known", verification_failures: int = 0, repeat_count: int = 0, required_sections: Iterable[str] = (), estimated_output_tokens: int = 0, input_cost_per_million: float | None = None, output_cost_per_million: float | None = None, cache_identity: dict[str, str] | None = None) -> OptimizationReport:
    original = [str(s) for s in sections if str(s).strip()]
    selected = select_relevant_context(query, original, budget_chars, required_sections=required_sections)
    selected_chars = sum(len(x.text) for x in selected)
    route = choose_model_tier(task=query, risk=risk, uncertainty=uncertainty, verification_failures=verification_failures)
    route = RouteDecision(route.tier, route.reason, estimate_tokens("\n".join(x.text for x in selected)), 0.0)
    return OptimizationReport(
        original_chars=sum(len(x) for x in original),
        selected_chars=selected_chars,
        estimated_input_tokens=route.estimated_input_tokens,
        compression_ratio=round(selected_chars / max(1, sum(len(x) for x in original)), 4),
        selected_sections=len(selected),
        route=route,
        script_candidate=should_use_script(query, repeat_count),
        estimated_output_tokens=output_tokens,
        estimated_cost_units=estimated_cost,
        cache_key=cache_key,
    )


def report_dict(report: OptimizationReport) -> dict[str, object]:
    return asdict(report)



_TIER_RANK = {"low": 0, "standard": 1, "high": 2}


def choose_capable_route(
    options: Iterable[ModelOption],
    *,
    required_capabilities: Iterable[str],
    privacy_classification: str,
    minimum_tier: str,
    estimated_input_tokens: int,
    estimated_output_tokens: int = 0,
) -> RouteDecision:
    """Recommend the least-cost verified option that satisfies hard constraints.

    This is a constrained recommendation helper, not a second provider router.
    The canonical provider lifecycle remains responsible for dispatch and
    runtime availability checks.
    """
    if minimum_tier not in _TIER_RANK:
        raise ValueError("minimum_tier must be low, standard or high")
    required = set(required_capabilities)
    candidates = []
    for option in options:
        if not option.availability_verified:
            continue
        if _TIER_RANK[option.tier] < _TIER_RANK[minimum_tier]:
            continue
        if not required.issubset(set(option.capabilities)):
            continue
        if privacy_classification not in option.privacy_levels:
            continue
        if estimated_input_tokens + estimated_output_tokens > option.max_context_tokens:
            continue
        cost = (estimated_input_tokens * option.input_cost_per_million
                + estimated_output_tokens * option.output_cost_per_million) / 1_000_000
        candidates.append((cost, option.estimated_latency_ms, option.provider, option.model, option))
    if not candidates:
        raise ValueError("no verified model option satisfies capability, privacy, tier and context constraints")
    cost, latency, _, _, chosen = min(candidates)
    return RouteDecision(
        chosen.tier, "lowest-cost-verified-capable-option",
        estimated_input_tokens, round(cost, 8), chosen.provider, chosen.model,
        estimated_output_tokens, latency,
    )


def optimization_cache_key(
    *,
    context_digest: str,
    model_id: str,
    tool_version: str,
    policy_digest: str,
    repository_digest: str,
    rubric_digest: str,
    privacy_scope: str,
) -> str:
    """Create a cache identity bound to every correctness/privacy boundary."""
    fields = (context_digest, model_id, tool_version, policy_digest,
              repository_digest, rubric_digest, privacy_scope)
    if any(not isinstance(value, str) or not value.strip() for value in fields):
        raise ValueError("all cache identity fields are required")
    payload = "\\0".join(fields).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compare_paired_runs(baseline: dict[str, object], candidate: dict[str, object]) -> dict[str, object]:
    """Compare matched runs; reject mismatched tasks, model tiers or rubric versions."""
    for key in ("task_digest", "model_tier", "rubric_digest"):
        if baseline.get(key) != candidate.get(key) or not baseline.get(key):
            raise ValueError(f"paired runs must share {key}")
    required = ("input_tokens", "output_tokens", "latency_ms", "quality_score", "policy_violations")
    for run in (baseline, candidate):
        if any(type(run.get(key)) not in (int, float) for key in required):
            raise ValueError("paired run metrics are incomplete or invalid")
    return {
        "matched": True,
        "input_token_savings": int(baseline["input_tokens"]) - int(candidate["input_tokens"]),
        "output_token_savings": int(baseline["output_tokens"]) - int(candidate["output_tokens"]),
        "latency_savings_ms": int(baseline["latency_ms"]) - int(candidate["latency_ms"]),
        "quality_delta": float(candidate["quality_score"]) - float(baseline["quality_score"]),
        "policy_violation_delta": int(candidate["policy_violations"]) - int(baseline["policy_violations"]),
        "promotion_eligible": (
            float(candidate["quality_score"]) >= float(baseline["quality_score"])
            and int(candidate["policy_violations"]) <= int(baseline["policy_violations"])
        ),
    }
