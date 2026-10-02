"""Bounded adaptive evolution of collaborative skill sets.

The evolver proposes small mutations to historically observed bundles. It is an
advisory optimizer: policy, availability, risk and resource gates remain owned
by the capability executioner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class SkillSetMutation:
    action: str
    parent_id: str
    parent_members: tuple[str, ...]
    members: tuple[str, ...]
    expected_delta: float
    reason: str

    @property
    def fingerprint(self) -> str:
        return "|".join(sorted(self.members))


class AdaptiveSkillSetEvolver:
    """Generate bounded add/remove/swap mutations from learned bundle evidence."""

    def __init__(self, *, min_expected_delta: float = 0.02, max_mutations: int = 8) -> None:
        self.min_expected_delta = max(0.0, min(1.0, float(min_expected_delta)))
        self.max_mutations = max(1, min(16, int(max_mutations)))

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @classmethod
    def _member_value(
        cls,
        name: str,
        history: Mapping[str, Mapping[str, Any]],
        contribution_history: Mapping[str, Mapping[str, Any]],
    ) -> float:
        h = history.get(name, {})
        c = contribution_history.get(name, {})
        evidence = cls._clamp(float(h.get("evidence_quality", 0.5)))
        success = cls._clamp(float(h.get("success_rate", 0.5)))
        confidence = cls._clamp(float(h.get("confidence", 0.25)))
        contribution = cls._clamp(float(c.get("evidence_quality", 0.5)))
        cost = min(1.0, max(0.0, float(h.get("avg_cost", 0.5))))
        latency = min(1.0, max(0.0, float(h.get("avg_latency", 0.5))) / 5.0)
        return 0.30 * evidence + 0.25 * success + 0.15 * confidence + 0.30 * contribution - 0.08 * cost - 0.05 * latency

    @classmethod
    def _bundle_value(
        cls,
        members: Sequence[str],
        history: Mapping[str, Mapping[str, Any]],
        contribution_history: Mapping[str, Mapping[str, Any]],
    ) -> float:
        if not members:
            return 0.0
        values = [cls._member_value(name, history, contribution_history) for name in members]
        sources = {str(history.get(name, {}).get("source", "")) for name in members}
        return sum(values) / len(values) + min(0.08, max(0, len(sources) - 1) * 0.02)

    @staticmethod
    def _parent_members(prior: Mapping[str, Any]) -> tuple[str, ...]:
        raw = prior.get("members", ())
        if isinstance(raw, str):
            return tuple(x for x in raw.split(",") if x)
        if isinstance(raw, Iterable):
            return tuple(str(x) for x in raw if str(x))
        return ()

    @staticmethod
    def _resource_cost(members: Sequence[str], options: Mapping[str, Any], history: Mapping[str, Mapping[str, Any]]) -> float:
        return sum(
            max(0.0, min(1.0, float(history.get(name, {}).get("avg_cost", getattr(options[name], "estimated_cost", 0.5)))))
            for name in members
        )

    @staticmethod
    def _context_cost(members: Sequence[str], options: Mapping[str, Any]) -> int:
        return sum(len(str(getattr(options[name], "instructions", ""))) for name in members)

    def propose(
        self,
        *,
        options: Sequence[Any],
        bundle_history: Mapping[str, Mapping[str, Any]],
        history: Mapping[str, Mapping[str, Any]],
        contribution_history: Mapping[str, Mapping[str, Any]],
        failed: Iterable[str] = (),
        resource_budget: float = 1.0,
        context_budget_chars: int = 8192,
    ) -> tuple[SkillSetMutation, ...]:
        blocked = set(failed)
        resource_budget = max(0.05, min(1.0, float(resource_budget)))
        context_budget_chars = max(512, min(32768, int(context_budget_chars)))
        available = {str(option.name): option for option in options if str(option.name) not in blocked and getattr(option, "available", True)}
        mutations: list[SkillSetMutation] = []
        for parent_id, prior in sorted(bundle_history.items()):
            parent = self._parent_members(prior)
            if not parent or any(name not in available for name in parent):
                continue
            if self._resource_cost(parent, available, history) > resource_budget or self._context_cost(parent, available) > context_budget_chars:
                continue
            if int(float(prior.get("samples", 0))) >= 3 and float(prior.get("collaboration_delta", 0.0) or 0.0) < 0:
                continue
            base = self._bundle_value(parent, history, contribution_history)
            outsiders = [
                name for name in available
                if name not in parent
            ]
            outsiders.sort(key=lambda name: (-self._member_value(name, history, contribution_history), name))

            if len(parent) > 1:
                for removed in sorted(parent):
                    candidate = tuple(name for name in parent if name != removed)
                    delta = self._bundle_value(candidate, history, contribution_history) - base
                    if delta >= self.min_expected_delta and self._resource_cost(candidate, available, history) <= resource_budget and self._context_cost(candidate, available) <= context_budget_chars:
                        mutations.append(SkillSetMutation(
                            "remove", parent_id, parent, candidate, delta,
                            f"remove low-value member {removed}",
                        ))

            if len(parent) < 3:
                for added in outsiders[:3]:
                    candidate = tuple(sorted(parent + (added,)))
                    delta = self._bundle_value(candidate, history, contribution_history) - base
                    if delta >= self.min_expected_delta:
                        mutations.append(SkillSetMutation(
                            "add", parent_id, parent, candidate, delta,
                            f"add complementary member {added}",
                        ))

            for removed in sorted(parent):
                for added in outsiders[:3]:
                    candidate = tuple(sorted([name for name in parent if name != removed] + [added]))
                    delta = self._bundle_value(candidate, history, contribution_history) - base
                    if delta >= self.min_expected_delta:
                        mutations.append(SkillSetMutation(
                            "swap", parent_id, parent, candidate, delta,
                            f"replace {removed} with {added}",
                        ))
        dedup: dict[tuple[str, tuple[str, ...]], SkillSetMutation] = {}
        for mutation in mutations:
            key = (mutation.action, mutation.members)
            if mutation.expected_delta > dedup.get(key, SkillSetMutation("", "", (), (), -1.0, "")).expected_delta:
                dedup[key] = mutation
        return tuple(sorted(
            dedup.values(),
            key=lambda item: (-item.expected_delta, len(item.members), item.action, item.members, item.parent_id),
        )[: self.max_mutations])
