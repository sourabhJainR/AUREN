"""Empirical intervention learning around AUREN's explicit causal graph."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from .causal_model import CausalLink, CausalModel


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Intervention:
    intervention_id: str
    cause_id: str
    value: object
    expected_effect: str


@dataclass(frozen=True)
class InterventionOutcome:
    intervention_id: str
    effect_id: str
    observed: bool
    evidence_id: str
    confidence: float


class CausalLearner:
    """Record interventions and update empirical support for causal links."""

    def __init__(self, causal: CausalModel, *, max_outcomes: int = 100_000) -> None:
        if not isinstance(causal, CausalModel):
            raise TypeError("causal must be a CausalModel instance")
        if max_outcomes < 1:
            raise ValueError("max_outcomes must be positive")
        self.causal = causal
        self.memory = causal.graph.memory
        self.project = causal.graph.project
        self.max_outcomes = max_outcomes
        with self.memory._lock, self.memory._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS causal_interventions(
                project TEXT NOT NULL, intervention_id TEXT NOT NULL, cause_id TEXT NOT NULL,
                value TEXT NOT NULL, expected_effect TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project, intervention_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS causal_outcomes(
                project TEXT NOT NULL, intervention_id TEXT NOT NULL, effect_id TEXT NOT NULL,
                observed INTEGER NOT NULL, evidence_id TEXT NOT NULL, confidence REAL NOT NULL,
                observed_at TEXT NOT NULL, PRIMARY KEY(project, intervention_id, effect_id, evidence_id))""")

    def propose(self, intervention: Intervention) -> None:
        if not intervention.intervention_id or not intervention.cause_id or not intervention.expected_effect:
            raise ValueError("intervention fields are required")
        with self.memory._lock, self.memory._connect() as db:
            existing = db.execute(
                "SELECT cause_id,value,expected_effect FROM causal_interventions WHERE project=? AND intervention_id=?",
                (self.project, intervention.intervention_id),
            ).fetchone()
            expected = (intervention.cause_id, json.dumps(intervention.value, sort_keys=True), intervention.expected_effect)
            if existing:
                if existing != expected:
                    raise ValueError("intervention_id already exists with different content")
                return
            db.execute("INSERT INTO causal_interventions VALUES(?,?,?,?,?,?)",
                       (self.project, intervention.intervention_id, intervention.cause_id,
                        expected[1], intervention.expected_effect, _utc()))

    def observe(self, outcome: InterventionOutcome) -> CausalLink | None:
        if not isinstance(outcome, InterventionOutcome):
            raise TypeError("outcome must be an InterventionOutcome")
        if not 0 <= outcome.confidence <= 1 or not outcome.evidence_id or not outcome.effect_id:
            raise ValueError("invalid intervention outcome")
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute("SELECT cause_id,expected_effect FROM causal_interventions WHERE project=? AND intervention_id=?",
                             (self.project, outcome.intervention_id)).fetchone()
            if row is None:
                raise KeyError("unknown intervention")
            if outcome.effect_id != row[1]:
                raise ValueError("observed effect does not match intervention expectation")
            existing = db.execute(
                "SELECT 1 FROM causal_outcomes WHERE project=? AND intervention_id=? AND effect_id=? AND evidence_id=?",
                (self.project, outcome.intervention_id, outcome.effect_id, outcome.evidence_id),
            ).fetchone()
            if existing:
                edges = self.causal.effects_of(row[0], limit=self.causal.max_links)
                return next((edge for edge in edges if edge.target_id == outcome.effect_id and edge.edge_id == f"intervention:{outcome.intervention_id}:{outcome.effect_id}"), None)
            count = db.execute("SELECT COUNT(*) FROM causal_outcomes WHERE project=?", (self.project,)).fetchone()[0]
            if count >= self.max_outcomes:
                raise ValueError("causal outcome budget exceeded")
            db.execute("INSERT INTO causal_outcomes VALUES(?,?,?,?,?,?,?)",
                       (self.project, outcome.intervention_id, outcome.effect_id, int(outcome.observed),
                        outcome.evidence_id, outcome.confidence, _utc()))
        if not outcome.observed:
            return None
        link = CausalLink(
            link_id=f"intervention:{outcome.intervention_id}:{outcome.effect_id}",
            cause_id=row[0], effect_id=outcome.effect_id,
            mechanism="empirical intervention observation", source="causal-learning",
            confidence=outcome.confidence, evidence=(outcome.evidence_id,), interventions=(outcome.intervention_id,),
        )
        return self.causal.record(link)


__all__ = ["CausalLearner", "Intervention", "InterventionOutcome"]
