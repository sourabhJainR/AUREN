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


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be non-empty trimmed text")
    return value


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


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
            _required_text(getattr(self, name), name)
        for name in ("task_manifest_digest", "split_digest", "candidate_digest",
                     "baseline_digest", "harness_digest", "environment_digest"):
            if not _valid_digest(getattr(self, name)):
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
    """An experiment identity or immutable evidence record was reused inconsistently."""


class ReproducibleExperimentStore:
    """Append-only manifests and outcomes with explicit unknown/unscored states."""

    def __init__(self, path: str | Path, *, evidence_verifier=None):
        if evidence_verifier is not None and not callable(evidence_verifier):
            raise TypeError("evidence_verifier must be callable")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.evidence_verifier = evidence_verifier
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS experiment_manifests(
                    experiment_id TEXT NOT NULL, manifest_digest TEXT NOT NULL,
                    manifest_json TEXT NOT NULL, parent_digest TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(experiment_id, manifest_digest));
                CREATE TABLE IF NOT EXISTS experiment_outcomes(
                    outcome_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL, status TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL, metrics_json TEXT NOT NULL,
                    outcome_digest TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS verified_lessons(
                    lesson_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL, outcome_digest TEXT NOT NULL,
                    lesson_digest TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TRIGGER IF NOT EXISTS experiment_manifests_no_update
                    BEFORE UPDATE ON experiment_manifests BEGIN
                    SELECT RAISE(ABORT, 'experiment manifests are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS experiment_manifests_no_delete
                    BEFORE DELETE ON experiment_manifests BEGIN
                    SELECT RAISE(ABORT, 'experiment manifests are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS experiment_outcomes_no_update
                    BEFORE UPDATE ON experiment_outcomes BEGIN
                    SELECT RAISE(ABORT, 'experiment outcomes are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS experiment_outcomes_no_delete
                    BEFORE DELETE ON experiment_outcomes BEGIN
                    SELECT RAISE(ABORT, 'experiment outcomes are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS verified_lessons_no_update
                    BEFORE UPDATE ON verified_lessons BEGIN
                    SELECT RAISE(ABORT, 'verified lessons are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS verified_lessons_no_delete
                    BEFORE DELETE ON verified_lessons BEGIN
                    SELECT RAISE(ABORT, 'verified lessons are append-only');
                END;
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def record_manifest(self, manifest: ExperimentManifest, *, parent_digest: str = "") -> str:
        if not isinstance(manifest, ExperimentManifest):
            raise TypeError("manifest must be an ExperimentManifest")
        if parent_digest and not _valid_digest(parent_digest):
            raise ValueError("parent_digest must be a lowercase SHA-256 digest")
        encoded = _canonical(manifest.as_dict())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if parent_digest:
                parent = db.execute(
                    "SELECT 1 FROM experiment_manifests WHERE experiment_id=? AND manifest_digest=?",
                    (manifest.experiment_id, parent_digest),
                ).fetchone()
                if parent is None:
                    raise ExperimentConflict("parent manifest must exist in the same experiment lineage")
            prior = db.execute(
                "SELECT manifest_json,parent_digest FROM experiment_manifests WHERE experiment_id=? AND manifest_digest=?",
                (manifest.experiment_id, manifest.digest),
            ).fetchone()
            if prior and prior != (encoded, parent_digest):
                raise ExperimentConflict("manifest identity collision")
            db.execute(
                "INSERT OR IGNORE INTO experiment_manifests(experiment_id,manifest_digest,manifest_json,parent_digest) VALUES(?,?,?,?)",
                (manifest.experiment_id, manifest.digest, encoded, parent_digest),
            )
        return manifest.digest

    def assert_resume_compatible(self, manifest: ExperimentManifest, *, expected_digest: str) -> None:
        if not isinstance(manifest, ExperimentManifest) or not _valid_digest(expected_digest):
            raise ValueError("a manifest and lowercase SHA-256 expected_digest are required")
        if manifest.digest != expected_digest:
            raise ExperimentConflict("resume manifest differs; fork a new experiment instead of mutating lineage")
        with self._connect() as db:
            row = db.execute(
                "SELECT manifest_json FROM experiment_manifests WHERE experiment_id=? AND manifest_digest=?",
                (manifest.experiment_id, expected_digest),
            ).fetchone()
        if row is None or row[0] != _canonical(manifest.as_dict()):
            raise ExperimentConflict("resume manifest is missing or incompatible")

    def record_outcome(self, *, outcome_id: str, manifest: ExperimentManifest, status: str,
                       evidence_refs: Sequence[str], metrics: Mapping[str, Any]) -> str:
        _required_text(outcome_id, "outcome_id")
        if not isinstance(manifest, ExperimentManifest):
            raise TypeError("manifest must be an ExperimentManifest")
        if status not in {"passed", "failed", "cancelled", "timed_out", "unknown", "unscored"}:
            raise ValueError("unsupported experiment outcome status")
        if not isinstance(evidence_refs, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in evidence_refs
        ):
            raise ValueError("evidence_refs must contain non-empty references")
        if not isinstance(metrics, Mapping):
            raise ValueError("metrics must be a mapping")
        evidence = list(dict.fromkeys(evidence_refs))
        metrics_json = _canonical(dict(metrics))
        payload = {
            "outcome_id": outcome_id, "manifest_digest": manifest.digest, "status": status,
            "evidence_refs": evidence, "metrics": json.loads(metrics_json),
        }
        digest = _digest(payload)
        evidence_json = _canonical(evidence)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute(
                "SELECT 1 FROM experiment_manifests WHERE experiment_id=? AND manifest_digest=?",
                (manifest.experiment_id, manifest.digest),
            ).fetchone()
            if not exists:
                raise ExperimentConflict("outcome requires a persisted manifest")
            prior = db.execute(
                "SELECT experiment_id,manifest_digest,status,evidence_refs_json,metrics_json,outcome_digest "
                "FROM experiment_outcomes WHERE outcome_id=?", (outcome_id,),
            ).fetchone()
            expected = (manifest.experiment_id, manifest.digest, status, evidence_json, metrics_json, digest)
            if prior and prior != expected:
                raise ExperimentConflict("outcome_id reused with changed lineage or evidence")
            db.execute(
                "INSERT OR IGNORE INTO experiment_outcomes(outcome_id,experiment_id,manifest_digest,status,evidence_refs_json,metrics_json,outcome_digest) VALUES(?,?,?,?,?,?,?)",
                (outcome_id, manifest.experiment_id, manifest.digest, status, evidence_json, metrics_json, digest),
            )
        return digest

    def promote_lesson(self, *, lesson_id: str, outcome_id: str, lesson: Mapping[str, Any]) -> str:
        _required_text(lesson_id, "lesson_id")
        _required_text(outcome_id, "outcome_id")
        if not isinstance(lesson, Mapping) or not lesson:
            raise ValueError("lesson must be a non-empty mapping")
        if self.evidence_verifier is None:
            raise PermissionError("trusted evidence verifier is required for lesson promotion")
        lesson_json = _canonical(dict(lesson))
        lesson_digest = _digest(json.loads(lesson_json))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT experiment_id,manifest_digest,status,evidence_refs_json,outcome_digest "
                "FROM experiment_outcomes WHERE outcome_id=?", (outcome_id,),
            ).fetchone()
            if row is None:
                raise ExperimentConflict("outcome does not exist")
            experiment_id, manifest_digest, status, evidence_json, outcome_digest = row
            evidence_refs = json.loads(evidence_json)
            try:
                verified = self.evidence_verifier(experiment_id, manifest_digest, evidence_refs)
            except Exception as exc:
                raise PermissionError("trusted evidence verification failed") from exc
            if status != "passed" or not evidence_refs or verified is not True:
                raise PermissionError("only independently verified passed outcomes with evidence can promote lessons")
            prior = db.execute(
                "SELECT experiment_id,manifest_digest,outcome_digest,lesson_digest "
                "FROM verified_lessons WHERE lesson_id=?", (lesson_id,),
            ).fetchone()
            expected = (experiment_id, manifest_digest, outcome_digest, lesson_digest)
            if prior and prior != expected:
                raise ExperimentConflict("lesson_id reused with changed lineage or content")
            db.execute(
                "INSERT OR IGNORE INTO verified_lessons(lesson_id,experiment_id,manifest_digest,outcome_digest,lesson_digest) VALUES(?,?,?,?,?)",
                (lesson_id, experiment_id, manifest_digest, outcome_digest, lesson_digest),
            )
        return lesson_digest
