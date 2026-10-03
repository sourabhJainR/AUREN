"""Conflict-aware persistent world-state view.

The base WorldModel intentionally returns the latest observation per predicate.
This layer prevents that convenience view from being mistaken for ground truth:
it exposes competing observations, confidence, provenance, and uncertainty.
No inference is silently promoted to fact.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json
from typing import Any, Sequence

from .world_model import Observation, WorldModel


@dataclass(frozen=True, slots=True)
class StateHypothesis:
    entity_id: str
    predicate: str
    value: Any
    support: float
    observation_ids: tuple[str, ...]
    sources: tuple[str, ...]
    conflict: bool


@dataclass(frozen=True, slots=True)
class WorldStateAssessment:
    entity_id: str
    predicate: str
    hypotheses: tuple[StateHypothesis, ...]
    selected: StateHypothesis | None
    uncertain: bool
    assessment_digest: str


class WorldStateConsistencyGuard:
    """Build an uncertainty-aware state assessment from persisted observations."""

    def assess(
        self,
        model: WorldModel,
        entity_id: str,
        predicate: str,
        *,
        max_observations: int = 100,
        min_confidence: float = 0.0,
        now: datetime | None = None,
        max_age_seconds: float | None = None,
    ) -> WorldStateAssessment:
        if not isinstance(model, WorldModel):
            raise TypeError("model must be a WorldModel")
        if not entity_id.strip() or not predicate.strip():
            raise ValueError("entity_id and predicate are required")
        if max_observations < 1 or not 0 <= min_confidence <= 1:
            raise ValueError("invalid observation limits")
        if max_age_seconds is not None and max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")

        current = now or datetime.now(timezone.utc)
        rows: Sequence[Observation] = model.history(entity_id, predicate, limit=max_observations)
        eligible=[]
        for row in rows:
            if row.confidence < min_confidence:
                continue
            observed=datetime.fromisoformat(row.observed_at.replace("Z","+00:00"))
            age=max(0.0,(current-observed).total_seconds())
            if max_age_seconds is not None and age > max_age_seconds:
                continue
            eligible.append((row, age))

        groups: dict[str, list[tuple[Observation,float]]] = {}
        for row, age in eligible:
            key=hashlib.sha256(json.dumps(row.value,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
            groups.setdefault(key,[]).append((row,age))

        hypotheses=[]
        for items in groups.values():
            support=sum(row.confidence/(1.0+age/86400.0) for row,age in items)
            hypotheses.append(StateHypothesis(
                entity_id, predicate, items[0][0].value,
                round(support,6),
                tuple(sorted(row.observation_id for row,_ in items)),
                tuple(sorted(set(row.source for row,_ in items))),
                len(groups)>1,
            ))
        hypotheses=tuple(sorted(hypotheses,key=lambda h:(-h.support,str(h.value))))
        selected=hypotheses[0] if hypotheses and len(hypotheses)==1 else None
        uncertain=len(hypotheses)!=1 or (selected is not None and selected.support < 1.0)
        payload={
            "entity_id":entity_id,"predicate":predicate,
            "hypotheses":[
                {"value":h.value,"support":h.support,"observation_ids":h.observation_ids,
                 "sources":h.sources,"conflict":h.conflict}
                for h in hypotheses
            ],
            "selected":selected.observation_ids if selected else (),
            "uncertain":uncertain,
        }
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
        return WorldStateAssessment(entity_id,predicate,hypotheses,selected,uncertain,digest)


__all__=["StateHypothesis","WorldStateAssessment","WorldStateConsistencyGuard"]
