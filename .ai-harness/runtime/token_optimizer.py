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
import math
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
    estimated_output_tokens: int | None = None
    estimated_latency_ms: int = 0


@dataclass(frozen=True)
class ModelOption:
    provider: str
    model: str
    tier: str
    input_cost_per_million: float | None
    output_cost_per_million: float | None
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
        for value in (self.input_cost_per_million, self.output_cost_per_million):
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(float(value)) or value < 0
            ):
                raise ValueError("model prices must be finite non-negative numbers or unknown")
        if self.max_context_tokens < 1 or self.estimated_latency_ms < 0:
            raise ValueError("invalid model capacity or latency")
        if type(self.availability_verified) is not bool:
            raise ValueError("availability_verified must be a boolean")
        if any(not isinstance(x, str) or not x.strip() for x in self.capabilities + self.privacy_levels):
            raise ValueError("capabilities and privacy levels must be non-empty strings")


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
    required_slices = [ContextSlice(item, 1.0, "required-preserved") for item in required]
    ranked: list[ContextSlice] = []
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
    selected: list[ContextSlice] = list(required_slices)
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
    normalized_uncertainty = uncertainty.lower()
    words = _terms(task)
    hard = {"architecture", "migration", "security", "incident", "concurrency", "distributed", "production"}
    simple = {"format", "rename", "summarize", "classification", "documentation", "typo", "changelog"}
    if normalized_risk in {"critical", "high"} or normalized_uncertainty in {"high", "unknown"} or verification_failures >= 2:
        tier, reason = "high", "risk-or-uncertainty-requires-stronger-reasoning"
    elif words & hard:
        tier, reason = "high", "task-signals-complex-reasoning"
    elif words & simple or len(words) <= 4:
        tier, reason = "low", "minimum-viable-model-for-simple-task"
    else:
        tier, reason = "standard", "default-model-for-normal-implementation"
    return RouteDecision(tier, reason, 0, None)


def should_use_script(task: str, repeat_count: int = 0) -> bool:
    """Recommend deterministic automation for repeatable mechanical work."""
    mechanical = ("format", "lint", "generate", "validate", "check", "sync", "convert", "inventory")
    return repeat_count >= 2 or any(word in task.lower() for word in mechanical)


def optimize(
    query: str,
    sections: Iterable[str],
    budget_chars: int,
    *,
    risk: str = "normal",
    uncertainty: str = "known",
    verification_failures: int = 0,
    repeat_count: int = 0,
    required_sections: Iterable[str] = (),
    estimated_output_tokens: int | None = None,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
    cache_identity: dict[str, str] | None = None,
    model_options: Iterable[ModelOption] | None = None,
    required_capabilities: Iterable[str] = (),
    privacy_classification: str = "private",
) -> OptimizationReport:
    original = [str(s) for s in sections if str(s).strip()]
    selected = select_relevant_context(query, original, budget_chars, required_sections=required_sections)
    selected_chars = len(query) + sum(len(x.text) for x in selected)
    base_route = choose_model_tier(
        task=query, risk=risk, uncertainty=uncertainty,
        verification_failures=verification_failures,
    )
    input_tokens = estimate_tokens(query + "\n" + "\n".join(x.text for x in selected))
    if estimated_output_tokens is not None and (
        type(estimated_output_tokens) is not int or estimated_output_tokens < 0
    ):
        raise ValueError("estimated_output_tokens must be a non-negative integer or unknown")
    output_tokens = estimated_output_tokens
    if model_options is not None:
        route = choose_capable_route(
            model_options,
            required_capabilities=required_capabilities,
            privacy_classification=privacy_classification,
            minimum_tier=base_route.tier,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
        )
    else:
        estimated_cost = None
        if (
            output_tokens is not None
            and input_cost_per_million is not None
            and output_cost_per_million is not None
        ):
            prices = (input_cost_per_million, output_cost_per_million)
            if any(type(value) not in (int, float) or not math.isfinite(float(value)) or value < 0 for value in prices):
                raise ValueError("token prices must be finite non-negative numbers")
            estimated_cost = round(
                (input_tokens * input_cost_per_million + output_tokens * output_cost_per_million) / 1_000_000,
                8,
            )
        route = RouteDecision(
            base_route.tier, base_route.reason, input_tokens, estimated_cost,
            estimated_output_tokens=output_tokens,
        )
    cache_key = optimization_cache_key(**cache_identity) if cache_identity is not None else None
    return OptimizationReport(
        original_chars=len(query) + sum(len(x) for x in original),
        selected_chars=selected_chars,
        estimated_input_tokens=input_tokens,
        compression_ratio=round(selected_chars / max(1, sum(len(x) for x in original)), 4),
        selected_sections=len(selected),
        route=route,
        script_candidate=should_use_script(query, repeat_count),
        estimated_output_tokens=output_tokens,
        estimated_cost_units=route.estimated_cost_units,
        selected_model=route.model or None,
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
    estimated_output_tokens: int | None = None,
) -> RouteDecision:
    """Recommend the least-cost verified option that satisfies hard constraints.

    This is a constrained recommendation helper, not a second provider router.
    The canonical provider lifecycle remains responsible for dispatch and
    runtime availability checks.
    """
    if minimum_tier not in _TIER_RANK:
        raise ValueError("minimum_tier must be low, standard or high")
    if not isinstance(privacy_classification, str) or not privacy_classification.strip():
        raise ValueError("privacy_classification is required")
    if type(estimated_input_tokens) is not int or estimated_input_tokens < 0:
        raise ValueError("estimated_input_tokens must be a non-negative integer")
    if estimated_output_tokens is not None and (
        type(estimated_output_tokens) is not int or estimated_output_tokens < 0
    ):
        raise ValueError("estimated_output_tokens must be a non-negative integer or unknown")
    required = set(required_capabilities)
    if any(not isinstance(item, str) or not item.strip() for item in required):
        raise ValueError("required capabilities must be non-empty strings")
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
        if estimated_input_tokens + (estimated_output_tokens or 0) > option.max_context_tokens:
            continue
        known_price = (
            estimated_output_tokens is not None
            and option.input_cost_per_million is not None
            and option.output_cost_per_million is not None
        )
        cost = (
            (estimated_input_tokens * option.input_cost_per_million
             + estimated_output_tokens * option.output_cost_per_million) / 1_000_000
            if known_price else None
        )
        # Prefer a price-backed option. If every eligible option has unknown
        # pricing, choose by latency but keep monetary cost explicitly unknown.
        candidates.append((0 if known_price else 1, cost if cost is not None else float("inf"),
                           option.estimated_latency_ms, option.provider, option.model, option, cost))
    if not candidates:
        raise ValueError("no verified model option satisfies capability, privacy, tier and context constraints")
    _, _, latency, _, _, chosen, cost = min(candidates, key=lambda item: item[:5])
    return RouteDecision(
        chosen.tier,
        "lowest-priced-verified-capable-option" if cost is not None else "verified-capable-option-price-unknown",
        estimated_input_tokens, round(cost, 8) if cost is not None else None,
        chosen.provider, chosen.model, estimated_output_tokens, latency,
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
    for key in (
        "task_digest", "model_tier", "model_id", "provider_version",
        "context_budget_tokens", "acceptance_digest", "rubric_digest", "policy_digest",
    ):
        if baseline.get(key) != candidate.get(key) or baseline.get(key) in (None, ""):
            raise ValueError(f"paired runs must share {key}")
    required = ("input_tokens", "output_tokens", "latency_ms", "quality_score", "policy_violations")
    for run in (baseline, candidate):
        if any(type(run.get(key)) not in (int, float) or not math.isfinite(float(run[key])) for key in required):
            raise ValueError("paired run metrics are incomplete or invalid")
        if int(run["input_tokens"]) < 0 or int(run["output_tokens"]) < 0 or int(run["latency_ms"]) < 0 or int(run["policy_violations"]) < 0:
            raise ValueError("paired run resource and policy metrics cannot be negative")
        if not 0 <= float(run["quality_score"]) <= 100:
            raise ValueError("paired run quality score must be between 0 and 100")
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
