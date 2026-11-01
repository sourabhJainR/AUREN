"""Reproducible, provenance-bound experiment manifests and lesson receipts.

This store records experiment lineage only. It does not grant execution authority,
verify external signatures, or promote lessons without a separately trusted verifier.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExperimentManifest:
    experiment_id: str
    task_manifest_digest: str
    seed: int
    split_digest: str
    candidate_digest: str
    baseline_digest: str
    harness_digest: str
    environment_digest: str
    model_provider: str
    model_version: str

    def __post_init__(self) -> None:
        for name in ("experiment_id", "model_provider", "model_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        for name in ("task_manifest_digest", "split_digest", "candidate_digest",
                     "baseline_digest", "harness_digest", "environment_digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")

    @property
    def digest(self) -> str:
        return _digest(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id, "task_manifest_digest": self.task_manifest_digest,
            "seed": self.seed, "split_digest": self.split_digest, "candidate_digest": self.candidate_digest,
            "baseline_digest": self.baseline_digest, "harness_digest": self.harness_digest,
            "environment_digest": self.environment_digest, "model_provider": self.model_provider,
            "model_version": self.model_version,
        }


class ExperimentConflict(RuntimeError):
    pass


class ReproducibleExperimentStore:
    """Append-only manifests and outcomes with explicit unknown/unscored states."""

    def __init__(self, path: str | Path, *, evidence_verifier=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.evidence_verifier = evidence_verifier
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS experiment_manifests(
                experiment_id TEXT NOT NULL, manifest_digest TEXT NOT NULL,
                manifest_json TEXT NOT NULL, parent_digest TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(experiment_id, manifest_digest))""")
            db.execute("""CREATE TABLE IF NOT EXISTS experiment_outcomes(
                outcome_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                manifest_digest TEXT NOT NULL, status TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL, metrics_json TEXT NOT NULL,
                outcome_digest TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("""CREATE TABLE IF NOT EXISTS verified_lessons(
                lesson_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                manifest_digest TEXT NOT NULL, outcome_digest TEXT NOT NULL,
                lesson_digest TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    def record_manifest(self, manifest: ExperimentManifest, *, parent_digest: str = "") -> str:
        if parent_digest and (len(parent_digest) != 64 or any(c not in "0123456789abcdef" for c in parent_digest)):
            raise ValueError("parent_digest must be a lowercase SHA-256 digest")
        encoded = _canonical(manifest.as_dict())
        with sqlite3.connect(self.path) as db:
            prior = db.execute(
                "SELECT manifest_json,parent_digest FROM experiment_manifests WHERE experiment_id=? AND manifest_digest=?",
                (manifest.experiment_id, manifest.digest),
            ).fetchone()
            if prior and prior != (encoded, parent_digest):
                raise ExperimentConflict("manifest identity collision")
            db.execute("INSERT OR IGNORE INTO experiment_manifests(experiment_id,manifest_digest,manifest_json,parent_digest) VALUES(?,?,?,?)",
                       (manifest.experiment_id, manifest.digest, encoded, parent_digest))
        return manifest.digest

    def assert_resume_compatible(self, manifest: ExperimentManifest, *, expected_digest: str) -> None:
        if manifest.digest != expected_digest:
            raise ExperimentConflict("resume manifest differs; fork a new experiment instead of mutating lineage")
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT manifest_json FROM experiment_manifests WHERE experiment_id=? AND manifest_digest=?",
                             (manifest.experiment_id, expected_digest)).fetchone()
        if row is None or row[0] != _canonical(manifest.as_dict()):
            raise ExperimentConflict("resume manifest is missing or incompatible")

    def record_outcome(self, *, outcome_id: str, manifest: ExperimentManifest, status: str,
                       evidence_refs: Sequence[str], metrics: Mapping[str, Any]) -> str:
        if status not in {"passed", "failed", "cancelled", "timed_out", "unknown", "unscored"}:
            raise ValueError("unsupported experiment outcome status")
        if not isinstance(evidence_refs, (list, tuple)) or any(not isinstance(x, str) or not x.strip() for x in evidence_refs):
            raise ValueError("evidence_refs must contain non-empty references")
        if not isinstance(metrics, Mapping):
            raise ValueError("metrics must be a mapping")
        evidence = list(dict.fromkeys(evidence_refs))
        payload = {"outcome_id": outcome_id, "manifest_digest": manifest.digest, "status": status,
                   "evidence_refs": evidence, "metrics": dict(metrics)}
        digest = _digest(payload)
        with sqlite3.connect(self.path) as db:
            exists = db.execute("SELECT 1 FROM experiment_manifests WHERE experiment_id=? AND manifest_digest=?",
                                (manifest.experiment_id, manifest.digest)).fetchone()
            if not exists:
                raise ExperimentConflict("outcome requires a persisted manifest")
            prior = db.execute("SELECT outcome_digest FROM experiment_outcomes WHERE outcome_id=?", (outcome_id,)).fetchone()
            if prior and prior[0] != digest:
                raise ExperimentConflict("outcome_id reused with changed evidence")
            db.execute("INSERT OR IGNORE INTO experiment_outcomes(outcome_id,experiment_id,manifest_digest,status,evidence_refs_json,metrics_json,outcome_digest) VALUES(?,?,?,?,?,?,?)",
                       (outcome_id, manifest.experiment_id, manifest.digest, status, _canonical(evidence), _canonical(dict(metrics)), digest))
        return digest

    def promote_lesson(self, *, lesson_id: str, outcome_id: str, lesson: Mapping[str, Any]) -> str:
        if self.evidence_verifier is None:
            raise PermissionError("trusted evidence verifier is required for lesson promotion")
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT experiment_id,manifest_digest,status,evidence_refs_json,outcome_digest FROM experiment_outcomes WHERE outcome_id=?",
                             (outcome_id,)).fetchone()
            if row is None:
                raise ExperimentConflict("outcome does not exist")
            experiment_id, manifest_digest, status, evidence_json, outcome_digest = row
            evidence_refs = json.loads(evidence_json)
            if status != "passed" or not evidence_refs or not self.evidence_verifier(experiment_id, manifest_digest, evidence_refs):
                raise PermissionError("only independently verified passed outcomes with evidence can promote lessons")
            lesson_digest = _digest(dict(lesson))
            db.execute("INSERT OR IGNORE INTO verified_lessons(lesson_id,experiment_id,manifest_digest,outcome_digest,lesson_digest) VALUES(?,?,?,?,?)",
                       (lesson_id, experiment_id, manifest_digest, outcome_digest, lesson_digest))
            prior = db.execute("SELECT lesson_digest FROM verified_lessons WHERE lesson_id=?", (lesson_id,)).fetchone()
            if prior is None or prior[0] != lesson_digest:
                raise ExperimentConflict("lesson_id reused with changed content")
        return lesson_digest
