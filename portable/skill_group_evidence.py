"""Evidence-driven execution-time adaptation of skill execution groups.

Group telemetry is treated as an empirical signal. It can replace or add skills
for the next execution, but it never bypasses capability, risk, resource or
verification policy owned by the executioner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


def group_key(members: Iterable[str]) -> str:
    return "|".join(sorted({str(name) for name in members if str(name)}))


@dataclass(frozen=True)
class SkillGroupEvidence:
    key: str
    members: tuple[str, ...]
    samples: int
    evidence_quality: float
    useful_evidence: float
    success_rate: float
    confidence: float
    avg_cost: float
    avg_latency: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "members": list(self.members),
            "samples": self.samples,
            "evidence_quality": round(self.evidence_quality, 3),
            "useful_evidence": round(self.useful_evidence, 3),
            "success_rate": round(self.success_rate, 3),
            "confidence": round(self.confidence, 3),
            "avg_cost": round(self.avg_cost, 3),
            "avg_latency": round(self.avg_latency, 3),
        }


@dataclass(frozen=True)
class SkillGroupAdaptation:
    action: str
    group_index: int
    before: tuple[str, ...]
    after: tuple[str, ...]
    reason: str
    evidence_delta: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "group_index": self.group_index,
            "before": list(self.before),
            "after": list(self.after),
            "reason": self.reason,
            "evidence_delta": round(self.evidence_delta, 3),
        }


def attribute_groups(
    *,
    skill_evidence: Sequence[Any],
    execution_groups: Sequence[Sequence[str]],
) -> tuple[SkillGroupEvidence, ...]:
    """Aggregate only the evidence observed inside each executed group."""
    by_name = {str(item.skill): item for item in skill_evidence}
    rows: list[SkillGroupEvidence] = []
    for index, group in enumerate(execution_groups, start=1):
        members = tuple(str(name) for name in group if str(name))
        observed = [by_name[name] for name in members if name in by_name]
        if not observed:
            continue
        quality = sum(float(item.evidence_quality) for item in observed) / len(observed)
        contribution = sum(float(item.contribution) for item in observed) / len(observed)
        useful_findings = sum(int(item.unique_findings) + int(item.evidence_signals) for item in observed)
        success = sum(1.0 for item in observed if str(item.status) == "passed") / len(observed)
        confidence = min(1.0, max(0.0, contribution))
        rows.append(
            SkillGroupEvidence(
                key=group_key(members),
                members=members,
                samples=1,
                evidence_quality=max(0.0, min(1.0, quality)),
                useful_evidence=max(0.0, min(1.0, contribution)),
                success_rate=success,
                confidence=confidence,
                avg_cost=0.0,
                avg_latency=0.0,
            )
        )
    return tuple(rows)


def adapt_execution_groups(
    *,
    execution_groups: Sequence[Sequence[str]],
    options: Sequence[Any],
    group_history: Mapping[str, Mapping[str, Any]],
    contribution_history: Mapping[str, Mapping[str, Any]],
    max_group_size: int = 3,
    min_samples: int = 2,
    weak_threshold: float = 0.30,
    strong_threshold: float = 0.65,
    min_delta: float = 0.05,
    context_budget_chars: int = 8192,
    resource_budget: float = 1.0,
    protected_names: Iterable[str] = (),
    max_risk: str = "high",
    network_allowed: bool = True,
    sandbox_available: bool = True,
    min_candidate_samples: int = 2,
    min_confidence: float = 0.55,
) -> tuple[tuple[tuple[str, ...], ...], tuple[SkillGroupAdaptation, ...]]:
    """Replace/add skills using group-level evidence, with bounded changes.

    A group is adapted only after repeated observations. Strong alternatives are
    sourced from independently observed skill contribution history. This is
    intentionally advisory: callers must run the resulting options through the
    normal capability policy before execution.
    """
    risk_order = {"low": 0, "medium": 1, "high": 2}
    risk_limit = risk_order.get(str(max_risk), -1)
    resource_budget = max(0.05, min(1.0, float(resource_budget)))
    by_name = {
        str(option.name): option
        for option in options
        if bool(getattr(option, "model_invocable", True))
        and risk_order.get(str(getattr(option, "risk", "low")).lower(), 99) <= risk_limit
        and (network_allowed or not bool(getattr(option, "requires_network", False)))
        and (sandbox_available or not bool(getattr(option, "requires_sandbox", False)))
    }
    adapted: list[tuple[str, ...]] = []
    changes: list[SkillGroupAdaptation] = []
    used = {name for group in execution_groups for name in group}
    protected = {str(name) for name in protected_names}

    def value(name: str) -> float:
        row = contribution_history.get(name, {})
        return max(0.0, min(1.0, float(row.get("evidence_quality", getattr(by_name.get(name), "evidence_quality", 0.5)))))

    alternatives = sorted(
        (name for name in by_name if name not in used),
        key=lambda name: (-value(name), name),
    )

    def candidate_allowed(candidate: Sequence[str], baseline_quality: float, baseline_success: float) -> bool:
        prior = group_history.get(group_key(candidate), {})
        samples = int(float(prior.get("samples", 0)))
        if samples < max(1, int(min_candidate_samples)):
            return True
        quality = float(prior.get("useful_evidence", prior.get("evidence_quality", 0.0)))
        success = float(prior.get("success_rate", 0.0))
        confidence = float(prior.get("confidence", 0.0))
        if samples >= max(1, int(min_candidate_samples)) and confidence < float(min_confidence):
            return False
        # Once a candidate group has evidence, require it to beat the current
        # group rather than trusting a strong individual member in isolation.
        return (
            quality >= max(weak_threshold, baseline_quality + min_delta)
            and success >= max(0.5, baseline_success)
        )

    for index, raw_group in enumerate(execution_groups, start=1):
        group = tuple(str(name) for name in raw_group if str(name))
        history = group_history.get(group_key(group), {})
        samples = int(float(history.get("samples", 0)))
        quality = max(0.0, min(1.0, float(history.get("useful_evidence", history.get("evidence_quality", 0.0)))))
        next_group = group
        if samples >= min_samples and quality < weak_threshold and group:
            removable = [name for name in group if name not in protected]
            if not removable:
                adapted.append(group)
                continue
            weakest = min(removable, key=lambda name: (value(name), name))
            replacement = next(
                (
                    name for name in alternatives
                    if name not in group
                    and candidate_allowed(tuple(sorted((set(group) - {weakest}) | {name})), quality, float(history.get("success_rate", 0.0)))
                ),
                None,
            )
            if replacement is not None and value(replacement) - value(weakest) >= min_delta:
                candidate = tuple(sorted((set(group) - {weakest}) | {replacement}))
                chars = sum(len(str(getattr(by_name.get(name), "instructions", ""))) for name in candidate)
                cost = sum(max(0.0, min(1.0, float(getattr(by_name.get(name), "estimated_cost", 0.5)))) for name in candidate)
                if chars <= max(512, min(32768, int(context_budget_chars))) and cost <= resource_budget:
                    next_group = candidate
                    changes.append(SkillGroupAdaptation(
                        "replace", index, group, candidate,
                        f"group evidence {quality:.2f} stayed below {weak_threshold:.2f}; "
                        f"replacement contribution improves by {value(replacement) - value(weakest):.2f}",
                        value(replacement) - value(weakest),
                    ))
        if next_group == group and samples >= min_samples and quality >= strong_threshold and len(group) < max_group_size:
            addition = next(
                (
                    name for name in alternatives
                    if name not in next_group
                    and candidate_allowed(tuple(sorted(next_group + (name,))), quality, float(history.get("success_rate", 0.0)))
                    and value(name) >= strong_threshold
                    and getattr(by_name.get(name), "phase", "") not in {
                        getattr(by_name.get(member), "phase", "") for member in next_group
                    }
                ),
                None,
            )
            if addition is not None:
                candidate = tuple(sorted(next_group + (addition,)))
                chars = sum(len(str(getattr(by_name.get(name), "instructions", ""))) for name in candidate)
                cost = sum(max(0.0, min(1.0, float(getattr(by_name.get(name), "estimated_cost", 0.5)))) for name in candidate)
                if chars <= max(512, min(32768, int(context_budget_chars))) and cost <= resource_budget:
                    next_group = candidate
                    changes.append(SkillGroupAdaptation(
                        "add", index, group, candidate,
                        f"group produced strong useful evidence {quality:.2f}; added complementary skill",
                        value(addition),
                    ))
        adapted.append(next_group)

    return tuple(adapted), tuple(changes)


__all__ = ["SkillGroupEvidence", "SkillGroupAdaptation", "group_key", "attribute_groups", "adapt_execution_groups"]
