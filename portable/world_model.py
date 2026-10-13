"""Durable, provenance-aware predictive world-model primitives for AUREN.

The world model stores observations in the canonical AUREN memory database. In
addition to deterministic current-state queries, it learns bounded empirical
state transitions conditioned on actions and can score predictions against
later observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any, Mapping
from uuid import uuid4

from .persistent_memory import PersistentMemory


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _value_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _value_digest(value: Any) -> str:
    return hashlib.sha256(_value_json(value).encode("utf-8")).hexdigest()


def _props(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("world-model properties must decode to an object")
    return decoded


def _timestamp(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("observed_at must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class Observation:
    observation_id: str
    entity_id: str
    predicate: str
    value: Any
    source: str
    confidence: float = 1.0
    observed_at: str = ""
    evidence: tuple[str, ...] = ()
    properties: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name, value in (("observation_id", self.observation_id), ("entity_id", self.entity_id),
                            ("predicate", self.predicate), ("source", self.source)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not self.observed_at:
            object.__setattr__(self, "observed_at", _utc())
        else:
            object.__setattr__(self, "observed_at", _timestamp(self.observed_at))
        if self.properties is None:
            object.__setattr__(self, "properties", {})
        if not isinstance(self.properties, Mapping):
            raise TypeError("properties must be a mapping")
        try:
            _value_json(self.value)
            json.dumps(dict(self.properties), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        except (TypeError, ValueError) as exc:
            raise TypeError("value and properties must be JSON-compatible") from exc


@dataclass(frozen=True)
class WorldFact:
    entity_id: str
    predicate: str
    value: Any
    source: str
    confidence: float
    observed_at: str
    observation_id: str
    evidence: tuple[str, ...] = ()
    properties: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class WorldPrediction:
    project: str
    prediction_id: str
    entity_id: str
    predicate: str
    action: str
    from_value: Any
    predicted_value: Any
    confidence: float
    evidence_observation_ids: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class PredictionError:
    prediction_id: str
    predicted_value: Any
    actual_value: Any
    absolute_match: bool
    error_digest: str
    measured_at: str


class WorldModel:
    """Bounded durable observations plus empirical action-conditioned prediction."""

    def __init__(self, memory: PersistentMemory, project: str, *, max_observations: int = 100_000) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        if max_observations < 1:
            raise ValueError("max_observations must be positive")
        self.memory = memory
        self.project = project
        self.max_observations = max_observations
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.memory.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS world_observations(
                    project TEXT NOT NULL,
                    observation_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    observed_at TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    properties TEXT NOT NULL,
                    PRIMARY KEY(project, observation_id))"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_world_observation_fact ON world_observations(project, entity_id, predicate, observed_at, observation_id)")
            db.execute(
                """CREATE TABLE IF NOT EXISTS world_predictions(
                    project TEXT NOT NULL,
                    prediction_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    action TEXT NOT NULL,
                    from_value TEXT NOT NULL,
                    predicted_value TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_ids TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project, prediction_id))"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS world_prediction_errors(
                    project TEXT NOT NULL,
                    prediction_id TEXT NOT NULL,
                    predicted_value TEXT NOT NULL,
                    actual_value TEXT NOT NULL,
                    absolute_match INTEGER NOT NULL,
                    error_digest TEXT NOT NULL,
                    measured_at TEXT NOT NULL,
                    PRIMARY KEY(project, prediction_id))"""
            )

    def observe(self, observation: Observation) -> Observation:
        value_json = _value_json(observation.value)
        evidence_json = json.dumps(sorted(set(observation.evidence)), separators=(",", ":"), ensure_ascii=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT entity_id,predicate,value,source,confidence,observed_at,evidence,properties FROM world_observations WHERE project=? AND observation_id=?",
                (self.project, observation.observation_id),
            ).fetchone()
            if existing:
                if existing != (observation.entity_id, observation.predicate, value_json, observation.source,
                                observation.confidence, observation.observed_at, evidence_json, _json(observation.properties)):
                    raise ValueError("observation_id already exists with different content")
                return observation
            count = db.execute("SELECT COUNT(*) FROM world_observations WHERE project=?", (self.project,)).fetchone()[0]
            if count >= self.max_observations:
                raise ValueError("world-model observation budget exceeded")
            db.execute(
                "INSERT INTO world_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self.project, observation.observation_id, observation.entity_id, observation.predicate,
                 value_json, observation.source, observation.confidence, observation.observed_at,
                 evidence_json, _json(observation.properties)),
            )
        return observation

    def current(self, entity_id: str, predicate: str | None = None, *, limit: int = 100) -> tuple[WorldFact, ...]:
        if limit < 1:
            return ()
        params: list[Any] = [self.project, entity_id]
        clause = "project=? AND entity_id=?"
        if predicate is not None:
            clause += " AND predicate=?"
            params.append(predicate)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT entity_id,predicate,value,source,confidence,observed_at,observation_id,evidence,properties FROM world_observations WHERE {clause} ORDER BY predicate,observed_at DESC,observation_id DESC",
                tuple(params),
            ).fetchall()
        latest: dict[str, WorldFact] = {}
        for row in rows:
            fact = WorldFact(row[0], row[1], json.loads(row[2]), row[3], float(row[4]), row[5], row[6],
                             tuple(json.loads(row[7])), _props(row[8]))
            latest.setdefault(fact.predicate, fact)
            if len(latest) >= limit:
                break
        return tuple(latest.values())

    def history(self, entity_id: str, predicate: str | None = None, *, limit: int = 100) -> tuple[Observation, ...]:
        if limit < 1:
            return ()
        params: list[Any] = [self.project, entity_id]
        clause = "project=? AND entity_id=?"
        if predicate is not None:
            clause += " AND predicate=?"
            params.append(predicate)
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT observation_id,entity_id,predicate,value,source,confidence,observed_at,evidence,properties FROM world_observations WHERE {clause} ORDER BY observed_at DESC,observation_id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return tuple(Observation(row[0], row[1], row[2], json.loads(row[3]), row[4], float(row[5]), row[6],
                                  tuple(json.loads(row[7])), _props(row[8])) for row in rows)

    def predict_next(self, entity_id: str, predicate: str, action: str, *, current_value: Any | None = None,
                     min_samples: int = 1) -> WorldPrediction | None:
        if not action.strip() or min_samples < 1:
            raise ValueError("action and min_samples must be valid")
        history = list(self.history(entity_id, predicate, limit=self.max_observations))
        if len(history) < 2:
            return None
        history.reverse()
        current = history[-1].value if current_value is None else current_value
        transitions: dict[str, list[Observation]] = {}
        current_digest = _value_digest(current)
        for previous, following in zip(history, history[1:]):
            if str((following.properties or {}).get("action", "")) != action:
                continue
            if _value_digest(previous.value) != current_digest:
                continue
            transitions.setdefault(_value_digest(following.value), []).append(following)
        matching_total = sum(len(items) for items in transitions.values())
        candidates = [(items[0].value, items) for items in transitions.values() if len(items) >= min_samples]
        if not candidates:
            return None
        predicted_value, evidence = max(candidates, key=lambda pair: (len(pair[1]), _value_digest(pair[0])))
        confidence = len(evidence) / matching_total if matching_total else 0.0
        prediction = WorldPrediction(
            project=self.project,
            prediction_id=uuid4().hex,
            entity_id=entity_id,
            predicate=predicate,
            action=action,
            from_value=current,
            predicted_value=predicted_value,
            confidence=confidence,
            evidence_observation_ids=tuple(item.observation_id for item in evidence),
            created_at=_utc(),
        )
        with self._connect() as db:
            db.execute(
                "INSERT INTO world_predictions VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self.project, prediction.prediction_id, prediction.entity_id, prediction.predicate,
                 prediction.action, _value_json(prediction.from_value), _value_json(prediction.predicted_value),
                 prediction.confidence, json.dumps(prediction.evidence_observation_ids, separators=(",", ":")),
                 prediction.created_at),
            )
        return prediction

    def score_prediction(self, prediction: WorldPrediction, actual_value: Any) -> PredictionError:
        if prediction.project != self.project:
            raise ValueError("prediction belongs to a different world-model project")
        digest = _value_digest({"predicted": prediction.predicted_value, "actual": actual_value})
        error = PredictionError(
            prediction_id=prediction.prediction_id,
            predicted_value=prediction.predicted_value,
            actual_value=actual_value,
            absolute_match=prediction.predicted_value == actual_value,
            error_digest=digest,
            measured_at=_utc(),
        )
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO world_prediction_errors VALUES(?,?,?,?,?,?,?)",
                (self.project, error.prediction_id, _value_json(error.predicted_value),
                 _value_json(error.actual_value), int(error.absolute_match),
                 error.error_digest, error.measured_at),
            )
        return error

    def prediction_calibration(self, *, predicate: str | None = None,
                               action: str | None = None, limit: int = 100) -> Mapping[str, float | int]:
        """Return verified prediction accuracy without changing model behavior."""
        if limit < 1:
            return {"samples": 0, "accuracy": 0.0}
        with self._connect() as db:
            clauses = ["e.project=?"]
            params: list[Any] = [self.project]
            if predicate is not None:
                clauses.append("p.predicate=?")
                params.append(predicate)
            if action is not None:
                clauses.append("p.action=?")
                params.append(action)
            query = f"""SELECT COUNT(*), COALESCE(AVG(e.absolute_match),0.0)
                        FROM world_prediction_errors e
                        JOIN world_predictions p
                          ON p.project=e.project AND p.prediction_id=e.prediction_id
                        WHERE {' AND '.join(clauses)}"""
            row = db.execute(query, tuple(params)).fetchone()
        return {"samples": int(row[0]), "accuracy": float(row[1])}

    def digest(self) -> str:
        with self._connect() as db:
            rows = db.execute(
                "SELECT observation_id,entity_id,predicate,value,source,confidence,observed_at,evidence,properties FROM world_observations WHERE project=? ORDER BY observation_id",
                (self.project,),
            ).fetchall()
        return hashlib.sha256(_json({"project": self.project, "observations": rows}).encode("utf-8")).hexdigest()[:16]


__all__ = ["Observation", "WorldFact", "WorldPrediction", "PredictionError", "WorldModel"]
