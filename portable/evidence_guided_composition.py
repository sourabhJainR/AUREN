"""Evidence-guided composition planner for open-ended capability invention.

The planner searches bounded combinations of already available capabilities to
address an externally evidenced gap. It is intentionally proposal-only and
delegates validation to the existing independent holdout machinery.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import hashlib, json
from typing import Sequence

from .autonomous_capability_invention import CapabilityComposition
from .open_ended_capability_discovery import CapabilityGap


@dataclass(frozen=True, slots=True)
class CompositionProposal:
    proposal_id: str
    gap_id: str
    composition: CapabilityComposition
    required_evidence: tuple[str, ...]
    validation_dimensions: tuple[str, ...]
    hypothesis: str
    rollback_condition: str
    proposal_digest: str


class EvidenceGuidedCompositionPlanner:
    """Rank bounded capability compositions against an evidenced gap."""

    def __init__(self, *, max_size: int = 3, budget: int = 12) -> None:
        if not 1 <= max_size <= 3:
            raise ValueError("max_size must be between 1 and 3")
        if budget < 1:
            raise ValueError("budget must be positive")
        self.max_size, self.budget = max_size, budget

    @staticmethod
    def _digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def propose(
        self,
        gap: CapabilityGap,
        available_capabilities: Sequence[str],
        *,
        strategy: str = "evidence-composition",
        resource_lanes: Sequence[str] = ("local", "agent"),
        verification_depths: Sequence[str] = ("deep", "independent"),
    ) -> tuple[CompositionProposal, ...]:
        if not gap.gap_id.strip():
            raise ValueError("gap identity is required")
        atoms = tuple(dict.fromkeys(str(x).strip() for x in available_capabilities if str(x).strip()))
        if not atoms:
            raise ValueError("available capabilities are required")
        lanes = tuple(dict.fromkeys(str(x).strip() for x in resource_lanes if str(x).strip())) or ("auto",)
        depths = tuple(dict.fromkeys(str(x).strip() for x in verification_depths if str(x).strip())) or ("independent",)
        dimensions = tuple(dict.fromkeys(("novel-domain",) + tuple(gap.failed_conditions) + ("constraint-shift",)))
        candidates: list[tuple[float, CapabilityComposition]] = []
        for size in range(1, min(self.max_size, len(atoms)) + 1):
            for combo in combinations(atoms, size):
                # Prefer compositions containing distinct skills; deterministic
                # ordering makes replay and regression analysis stable.
                coverage = len(set(combo) & set(gap.failed_conditions)) / max(1, len(gap.failed_conditions))
                for lane in lanes:
                    for depth in depths:
                        comp = CapabilityComposition(
                            f"{strategy}:{lane}:{depth}:{'+'.join(combo)}",
                            combo, strategy, lane, depth,
                        )
                        candidates.append((coverage + 0.01 * size, comp))
        candidates.sort(key=lambda x: (-x[0], len(x[1].capabilities), x[1].id))
        proposals=[]
        for score, comp in candidates[:self.budget]:
            seed=self._digest({
                "gap": gap.gap_id, "composition": comp.id,
                "evidence": gap.source_evidence, "dimensions": dimensions,
            })
            proposals.append(CompositionProposal(
                f"composition-{seed[:16]}",
                gap.gap_id,
                comp,
                tuple(sorted(set(gap.source_evidence))),
                dimensions,
                f"Composing {', '.join(comp.capabilities)} should reduce {gap.gap_id} "
                f"while preserving transfer across {', '.join(gap.domains)}.",
                "rollback if the fresh independent holdout regresses or unrelated-domain transfer degrades",
                seed,
            ))
        return tuple(proposals)


__all__=["CompositionProposal","EvidenceGuidedCompositionPlanner"]
