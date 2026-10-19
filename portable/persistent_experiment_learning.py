"""Persistent, provenance-aware learning receipts for future AUREN runs.

Experiment outcomes are never promoted just because a score is high. This store
records the exact experiment manifest, result, derived curriculum signal, and
evidence lineage in one SQLite transaction. Candidate lessons require a separate
promotion receipt before retrieval; incompatible, retracted, contested, or
unscored lessons are never returned as reusable positive guidance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import time
from typing import Any, Callable, Mapping, Sequence

from .adaptive_experiment_controller import AdaptiveExperimentResult
from .engineering_episode import EngineeringEpisode, EpisodePhase
from .experiment_learning_bridge import CurriculumLearningSignal, ExperimentLearningBridge
from .persistent_evidence_graph import EvidenceNode, PersistentEvidenceGraph
from .persistent_memory import PersistentMemory


class LearningStoreConflict(RuntimeError):
    """Immutable learning identity was reused with different content."""


class LearningEvidenceError(ValueError):
    """Required independent evidence verification was missing or rejected."""


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    experiment_id: str
    capability: str
    intervention_digest: str
    task_digest: str
    task_split_digest: str
    harness_digest: str
    model_id: str
    provider_id: str
    executor_id: str
    evaluator_id: str
    policy_digest: str
    environment_digest: str
    budget_digest: str
    isolation_attestation_digest: str
    seed: int

    def __post_init__(self) -> None:
        for name in (
            "experiment_id", "capability", "intervention_digest", "task_digest",
            "task_split_digest", "harness_digest", "model_id", "provider_id",
            "executor_id", "evaluator_id", "policy_digest", "environment_digest",
            "budget_digest", "isolation_attestation_digest",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        return _canonical_digest({"schema": "auren-experiment-manifest-v1", **self.as_dict()})

    @property
    def compatibility_digest(self) -> str:
        # Per-run ids, seed and attestation nonce are deliberately excluded; execution
        # stack, task split, policy and environment must still match exactly.
        return _canonical_digest({
            "schema": "auren-learning-compatibility-v1",
            "task_split_digest": self.task_split_digest,
            "harness_digest": self.harness_digest,
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "executor_id": self.executor_id,
            "evaluator_id": self.evaluator_id,
            "policy_digest": self.policy_digest,
            "environment_digest": self.environment_digest,
            "budget_digest": self.budget_digest,
        })

    @property
    def lesson_key(self) -> str:
        return _canonical_digest({
            "capability": self.capability,
            "task_split_digest": self.task_split_digest,
            "compatibility_digest": self.compatibility_digest,
        })


@dataclass(frozen=True, slots=True)
class PersistentLearningReceipt:
    receipt_id: str
    experiment_id: str
    manifest_digest: str
    result_digest: str
    signal_digest: str
    capability: str
    action: str
    disposition: str
    compatibility_digest: str
    lesson_key: str
    evidence_refs: tuple[str, ...]
    evidence_verified: bool
    created_at: float
    record_digest: str
    episode_digest: str = ""


class PersistentExperimentLearningStore:
    """Store experiment outcomes and gate their reuse with evidence and compatibility."""

    def __init__(
        self,
        memory: PersistentMemory,
        project: str,
        *,
        verify_evidence: Callable[[tuple[str, ...], ExperimentManifest, AdaptiveExperimentResult], bool],
        verify_promotion: Callable[[str, str, str], bool],
        verify_retraction: Callable[[str, str, str], bool],
    ) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be PersistentMemory")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        for name, callback in (
            ("verify_evidence", verify_evidence),
            ("verify_promotion", verify_promotion),
            ("verify_retraction", verify_retraction),
        ):
            if not callable(callback):
                raise ValueError(f"trusted {name} callback is required")
        self.memory = memory
        self.project = project.strip()
        self.verify_evidence = verify_evidence
        self.verify_promotion = verify_promotion
        self.verify_retraction = verify_retraction
        self.graph = PersistentEvidenceGraph(memory, self.project)
        with memory._lock, memory._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS experiment_learning_receipts (
                    project TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    result_digest TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    signal_digest TEXT NOT NULL,
                    signal_json TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    action TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    compatibility_digest TEXT NOT NULL,
                    lesson_key TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    evidence_verified INTEGER NOT NULL,
                    episode_digest TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    record_digest TEXT NOT NULL,
                    PRIMARY KEY(project, receipt_id),
                    UNIQUE(project, experiment_id, manifest_digest)
                );
                CREATE INDEX IF NOT EXISTS idx_learning_compatible
                    ON experiment_learning_receipts(project, capability, compatibility_digest, disposition, created_at);
                CREATE TABLE IF NOT EXISTS experiment_learning_promotions (
                    project TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    reviewer_id TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    promotion_digest TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(project, receipt_id)
                );
                CREATE TABLE IF NOT EXISTS experiment_learning_retractions (
                    project TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    retraction_digest TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(project, receipt_id)
                );
                CREATE TABLE IF NOT EXISTS experiment_learning_conflicts (
                    project TEXT NOT NULL,
                    lesson_key TEXT NOT NULL,
                    candidate_receipt_id TEXT NOT NULL,
                    negative_receipt_id TEXT NOT NULL,
                    conflict_digest TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(project, lesson_key, candidate_receipt_id, negative_receipt_id)
                );
            """)

    @staticmethod
    def _safe_json(value: Any, field: str) -> str:
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be finite JSON-compatible data") from exc

    def _insert_node(self, db: Any, kind: str, digest: str, metadata: Mapping[str, str] | None = None) -> EvidenceNode:
        node = EvidenceNode(
            self.graph.node_id(kind, digest), kind, digest,
            tuple(sorted((metadata or {}).items())),
        )
        metadata_json = json.dumps(dict(node.metadata), sort_keys=True, separators=(",", ":"), allow_nan=False)
        db.execute(
            "INSERT OR IGNORE INTO evidence_nodes(project,node_id,kind,digest,metadata_json) VALUES(?,?,?,?,?)",
            (self.project, node.node_id, node.kind, node.digest, metadata_json),
        )
        row = db.execute(
            "SELECT kind,digest,metadata_json FROM evidence_nodes WHERE project=? AND node_id=?",
            (self.project, node.node_id),
        ).fetchone()
        if row is None or row[0] != node.kind or row[1] != node.digest or json.loads(row[2]) != dict(node.metadata):
            raise LearningStoreConflict("content-addressed evidence node conflicts with existing lineage")
        return node

    def _insert_edge(self, db: Any, source: EvidenceNode, relation: str, target: EvidenceNode) -> None:
        edge_digest = self.graph.edge_digest(source.node_id, relation, target.node_id)
        for node in (source, target):
            row = db.execute(
                "SELECT kind,digest FROM evidence_nodes WHERE project=? AND node_id=?",
                (self.project, node.node_id),
            ).fetchone()
            if row is None or row[0] != node.kind or row[1] != node.digest:
                raise LearningStoreConflict("learning lineage edge references an unregistered node")
        db.execute(
            "INSERT OR IGNORE INTO evidence_edges(project,source_id,relation,target_id,edge_digest) VALUES(?,?,?,?,?)",
            (self.project, source.node_id, relation, target.node_id, edge_digest),
        )

    @staticmethod
    def _receipt_from_row(row: Sequence[Any]) -> PersistentLearningReceipt:
        return PersistentLearningReceipt(
            receipt_id=row[0], experiment_id=row[1], manifest_digest=row[2],
            result_digest=row[3], signal_digest=row[4], capability=row[5],
            action=row[6], disposition=row[7], compatibility_digest=row[8],
            lesson_key=row[9], evidence_refs=tuple(json.loads(row[10])),
            evidence_verified=bool(row[11]), created_at=float(row[12]),
            record_digest=row[13], episode_digest=row[14],
        )

    def get_receipt(self, receipt_id: str) -> PersistentLearningReceipt | None:
        with self.memory._lock, self.memory._connection() as db:
            row = db.execute(
                "SELECT receipt_id,experiment_id,manifest_digest,result_digest,signal_digest,capability,action,disposition,"
                "compatibility_digest,lesson_key,evidence_refs_json,evidence_verified,created_at,record_digest,episode_digest "
                "FROM experiment_learning_receipts WHERE project=? AND receipt_id=?",
                (self.project, receipt_id),
            ).fetchone()
        return self._receipt_from_row(row) if row else None

    def record(
        self,
        *,
        manifest: ExperimentManifest,
        result: AdaptiveExperimentResult,
        signal: CurriculumLearningSignal,
        evidence_refs: Sequence[str],
        episode: EngineeringEpisode | None = None,
        domain_lifts: tuple[tuple[str, float], ...] = (),
    ) -> PersistentLearningReceipt:
        if not isinstance(manifest, ExperimentManifest) or not isinstance(result, AdaptiveExperimentResult):
            raise TypeError("manifest and AdaptiveExperimentResult are required")
        if not isinstance(signal, CurriculumLearningSignal):
            raise TypeError("CurriculumLearningSignal is required")
        if manifest.experiment_id != result.experiment_id or signal.experiment_id != result.experiment_id:
            raise LearningStoreConflict("experiment identities do not match")
        if manifest.intervention_digest != result.intervention_digest:
            raise LearningStoreConflict("experiment manifest intervention digest mismatch")
        if manifest.capability != signal.capability or signal.evidence_digest != result.result_digest:
            raise LearningStoreConflict("learning signal is not bound to the supplied result/capability")
        expected_signal = ExperimentLearningBridge().derive(
            capability=manifest.capability, result=result, domain_lifts=domain_lifts,
        )
        if signal.signal_digest != expected_signal.signal_digest:
            raise LearningStoreConflict("curriculum signal does not match the verified experiment result and domain lifts")
        if isinstance(evidence_refs, (str, bytes)) or not isinstance(evidence_refs, Sequence):
            raise LearningEvidenceError("evidence_refs must be a sequence of opaque reference strings")
        if any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs):
            raise LearningEvidenceError("evidence references must be non-empty strings")
        refs = tuple(dict.fromkeys(ref.strip() for ref in evidence_refs))
        if not refs:
            raise LearningEvidenceError("evidence references are required; provide at least one non-empty reference")
        for domain, lift in domain_lifts:
            if not isinstance(domain, str) or not domain.strip():
                raise ValueError("domain lift names must be non-empty strings")
            if isinstance(lift, bool) or not isinstance(lift, (int, float)) or not math.isfinite(lift):
                raise ValueError("domain lifts must be finite numbers")
        episode_digest = ""
        if episode is not None:
            if not isinstance(episode, EngineeringEpisode):
                raise TypeError("episode must be an EngineeringEpisode")
            if episode.project != self.project or episode.phase not in {EpisodePhase.COMPLETED, EpisodePhase.FAILED}:
                raise LearningEvidenceError("learning may only attach to a terminal episode in this project")
            episode_digest = episode.digest

        try:
            evidence_verified = self.verify_evidence(refs, manifest, result) is True
        except Exception:
            evidence_verified = False
        if not evidence_verified:
            disposition = "unscored"
        elif result.regression_detected or result.rollback_recommended or signal.action == "rollback":
            disposition = "negative"
        elif not result.evidence_sufficient or signal.action == "insufficient-evidence":
            disposition = "unscored"
        else:
            disposition = "candidate"

        manifest_json = self._safe_json(manifest.as_dict(), "manifest")
        result_json = self._safe_json(asdict(result), "result")
        signal_json = self._safe_json(asdict(signal), "signal")
        refs_json = self._safe_json(refs, "evidence_refs")
        receipt_id = _canonical_digest({"project": self.project, "experiment_id": manifest.experiment_id, "manifest_digest": manifest.digest})
        created_at = time.time()
        payload = {
            "receipt_id": receipt_id, "experiment_id": manifest.experiment_id,
            "manifest_digest": manifest.digest, "result_digest": result.result_digest,
            "signal_digest": signal.signal_digest, "capability": manifest.capability,
            "action": signal.action, "disposition": disposition,
            "compatibility_digest": manifest.compatibility_digest, "lesson_key": manifest.lesson_key,
            "evidence_refs": refs, "evidence_verified": evidence_verified,
            "episode_digest": episode_digest,
        }
        record_digest = _canonical_digest(payload)

        with self.memory._lock, self.memory._connection() as db:
            existing = db.execute(
                "SELECT receipt_id,experiment_id,manifest_digest,result_digest,signal_digest,capability,action,disposition,"
                "compatibility_digest,lesson_key,evidence_refs_json,evidence_verified,created_at,record_digest,episode_digest "
                "FROM experiment_learning_receipts WHERE project=? AND experiment_id=? AND manifest_digest=?",
                (self.project, manifest.experiment_id, manifest.digest),
            ).fetchone()
            if existing is not None:
                receipt = self._receipt_from_row(existing)
                if (
                    receipt.result_digest != result.result_digest
                    or receipt.signal_digest != signal.signal_digest
                    or receipt.evidence_refs != refs
                    or receipt.episode_digest != episode_digest
                ):
                    raise LearningStoreConflict("same experiment manifest was replayed with different outcome/evidence")
                return receipt

            db.execute(
                "INSERT INTO experiment_learning_receipts "
                "(project,receipt_id,experiment_id,manifest_digest,manifest_json,result_digest,result_json,signal_digest,signal_json,"
                "capability,action,disposition,compatibility_digest,lesson_key,evidence_refs_json,evidence_verified,episode_digest,created_at,record_digest) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.project, receipt_id, manifest.experiment_id, manifest.digest, manifest_json,
                 result.result_digest, result_json, signal.signal_digest, signal_json, manifest.capability,
                 signal.action, disposition, manifest.compatibility_digest, manifest.lesson_key,
                 refs_json, int(evidence_verified), episode_digest, created_at, record_digest),
            )

            manifest_node = self._insert_node(db, "experiment_manifest", manifest.digest, {
                "capability": manifest.capability, "compatibility_digest": manifest.compatibility_digest,
            })
            result_node = self._insert_node(db, "experiment_result", result.result_digest, {
                "experiment_id": manifest.experiment_id,
            })
            signal_node = self._insert_node(db, "curriculum_signal", signal.signal_digest, {
                "action": signal.action, "capability": signal.capability,
            })
            receipt_node = self._insert_node(db, "learning_receipt", receipt_id, {
                "disposition": disposition, "capability": manifest.capability,
            })
            self._insert_edge(db, manifest_node, "produced_result", result_node)
            self._insert_edge(db, result_node, "derived_signal", signal_node)
            self._insert_edge(db, signal_node, "persisted_as", receipt_node)
            for ref in refs:
                ref_digest = _canonical_digest({"external_evidence_ref": ref})
                ref_node = self._insert_node(db, "external_evidence_ref", ref_digest, {"ref_kind": "opaque"})
                self._insert_edge(db, result_node, "supported_by", ref_node)
            if episode is not None:
                episode_node = self._insert_node(db, "engineering_episode", episode.digest, {
                    "episode_id": episode.episode_id, "phase": episode.phase.value,
                })
                self._insert_edge(db, receipt_node, "associated_episode", episode_node)

            if disposition in {"candidate", "negative"}:
                opposite = "negative" if disposition == "candidate" else "candidate"
                rows = db.execute(
                    "SELECT receipt_id,disposition FROM experiment_learning_receipts "
                    "WHERE project=? AND lesson_key=? AND receipt_id<>? AND disposition=?",
                    (self.project, manifest.lesson_key, receipt_id, opposite),
                ).fetchall()
                for other_id, other_disposition in rows:
                    candidate_id = receipt_id if disposition == "candidate" else other_id
                    negative_id = receipt_id if disposition == "negative" else other_id
                    conflict_digest = _canonical_digest([self.project, manifest.lesson_key, candidate_id, negative_id])
                    db.execute(
                        "INSERT OR IGNORE INTO experiment_learning_conflicts VALUES(?,?,?,?,?,?)",
                        (self.project, manifest.lesson_key, candidate_id, negative_id, conflict_digest, created_at),
                    )

        receipt = self.get_receipt(receipt_id)
        if receipt is None:
            raise RuntimeError("learning receipt committed but could not be reloaded")
        return receipt

    def promote_candidate(self, receipt_id: str, *, reviewer_id: str, evidence_ref: str) -> str:
        reviewer = self._nonempty(reviewer_id, "reviewer_id")
        ref = self._nonempty(evidence_ref, "evidence_ref")
        try:
            verified = self.verify_promotion(receipt_id, reviewer, ref) is True
        except Exception:
            verified = False
        if not verified:
            raise LearningEvidenceError("independent promotion evidence was not verified")
        now = time.time()
        promotion_digest = _canonical_digest({
            "receipt_id": receipt_id, "reviewer_id": reviewer, "evidence_ref": ref,
        })
        with self.memory._lock, self.memory._connection() as db:
            row = db.execute(
                "SELECT disposition,lesson_key,evidence_verified FROM experiment_learning_receipts WHERE project=? AND receipt_id=?",
                (self.project, receipt_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"learning receipt {receipt_id!r} does not exist")
            if row[0] != "candidate" or not row[2]:
                raise LearningEvidenceError("only evidence-verified candidates can be promoted")
            if db.execute(
                "SELECT 1 FROM experiment_learning_retractions WHERE project=? AND receipt_id=?",
                (self.project, receipt_id),
            ).fetchone():
                raise LearningEvidenceError("retracted learning receipt cannot be promoted")
            if db.execute(
                "SELECT 1 FROM experiment_learning_conflicts WHERE project=? AND lesson_key=?",
                (self.project, row[1]),
            ).fetchone():
                raise LearningEvidenceError("contested lesson cannot be promoted")
            existing = db.execute(
                "SELECT reviewer_id,evidence_ref,promotion_digest FROM experiment_learning_promotions WHERE project=? AND receipt_id=?",
                (self.project, receipt_id),
            ).fetchone()
            if existing:
                if existing[0] == reviewer and existing[1] == ref and existing[2] == promotion_digest:
                    return promotion_digest
                raise LearningStoreConflict("promotion receipt already exists with different reviewer/evidence")
            db.execute(
                "INSERT INTO experiment_learning_promotions VALUES(?,?,?,?,?,?)",
                (self.project, receipt_id, reviewer, ref, promotion_digest, now),
            )
            receipt_node = self._insert_node(db, "learning_receipt", receipt_id, {
                "disposition": "candidate", "capability": self._capability_for(db, receipt_id),
            })
            promotion_node = self._insert_node(db, "learning_promotion", promotion_digest, {
                "reviewer_id": reviewer, "evidence_ref_digest": _canonical_digest({"ref": ref}),
            })
            self._insert_edge(db, receipt_node, "promoted_by", promotion_node)
        return promotion_digest

    def retract(self, receipt_id: str, *, reason: str, evidence_ref: str) -> str:
        why = self._nonempty(reason, "reason")
        ref = self._nonempty(evidence_ref, "evidence_ref")
        try:
            verified = self.verify_retraction(receipt_id, why, ref) is True
        except Exception:
            verified = False
        if not verified:
            raise LearningEvidenceError("independent retraction evidence was not verified")
        now = time.time()
        retraction_digest = _canonical_digest({"receipt_id": receipt_id, "reason": why, "evidence_ref": ref})
        with self.memory._lock, self.memory._connection() as db:
            row = db.execute(
                "SELECT capability,disposition FROM experiment_learning_receipts WHERE project=? AND receipt_id=?",
                (self.project, receipt_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"learning receipt {receipt_id!r} does not exist")
            existing = db.execute(
                "SELECT reason,evidence_ref,retraction_digest FROM experiment_learning_retractions WHERE project=? AND receipt_id=?",
                (self.project, receipt_id),
            ).fetchone()
            if existing:
                if tuple(existing) == (why, ref, retraction_digest):
                    return retraction_digest
                raise LearningStoreConflict("learning receipt already has a different retraction")
            db.execute(
                "INSERT INTO experiment_learning_retractions VALUES(?,?,?,?,?,?)",
                (self.project, receipt_id, why, ref, retraction_digest, now),
            )
            receipt_node = self._insert_node(db, "learning_receipt", receipt_id, {
                "disposition": row[1], "capability": row[0],
            })
            retraction_node = self._insert_node(db, "learning_retraction", retraction_digest, {
                "reason_digest": _canonical_digest({"reason": why}),
                "evidence_ref_digest": _canonical_digest({"ref": ref}),
            })
            self._insert_edge(db, receipt_node, "retracted_by", retraction_node)
        return retraction_digest

    def retrieve_compatible(
        self,
        manifest: ExperimentManifest,
        *,
        capability: str | None = None,
        limit: int = 20,
        max_age_seconds: float = 30 * 24 * 60 * 60,
    ) -> tuple[PersistentLearningReceipt, ...]:
        if not isinstance(manifest, ExperimentManifest):
            raise TypeError("an ExperimentManifest is required")
        if type(limit) is not int or limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be finite and positive")
        cap = capability or manifest.capability
        if cap != manifest.capability:
            raise ValueError("requested capability does not match the compatibility manifest")
        cutoff = time.time() - float(max_age_seconds)
        with self.memory._lock, self.memory._connection() as db:
            rows = db.execute(
                "SELECT r.receipt_id,r.experiment_id,r.manifest_digest,r.result_digest,r.signal_digest,r.capability,r.action,"
                "r.disposition,r.compatibility_digest,r.lesson_key,r.evidence_refs_json,r.evidence_verified,r.created_at,r.record_digest,r.episode_digest "
                "FROM experiment_learning_receipts AS r "
                "JOIN experiment_learning_promotions AS p ON p.project=r.project AND p.receipt_id=r.receipt_id "
                "WHERE r.project=? AND r.capability=? AND r.compatibility_digest=? AND r.disposition='candidate' "
                "AND r.evidence_verified=1 AND r.created_at>=? "
                "AND NOT EXISTS (SELECT 1 FROM experiment_learning_retractions x WHERE x.project=r.project AND x.receipt_id=r.receipt_id) "
                "AND NOT EXISTS (SELECT 1 FROM experiment_learning_conflicts c WHERE c.project=r.project AND c.lesson_key=r.lesson_key) "
                "ORDER BY r.created_at DESC,r.receipt_id LIMIT ?",
                (self.project, cap, manifest.compatibility_digest, cutoff, limit),
            ).fetchall()
        return tuple(self._receipt_from_row(row) for row in rows)

    @staticmethod
    def _nonempty(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be non-empty text")
        return value.strip()

    def _capability_for(self, db: Any, receipt_id: str) -> str:
        row = db.execute(
            "SELECT capability FROM experiment_learning_receipts WHERE project=? AND receipt_id=?",
            (self.project, receipt_id),
        ).fetchone()
        if row is None:
            raise KeyError(receipt_id)
        return str(row[0])


__all__ = [
    "ExperimentManifest", "PersistentLearningReceipt", "PersistentExperimentLearningStore",
    "LearningStoreConflict", "LearningEvidenceError",
]
