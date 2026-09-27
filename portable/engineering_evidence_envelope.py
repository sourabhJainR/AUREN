"""Canonical immutable envelope for end-to-end engineering evidence lineage.

The envelope stores identifiers and digests, not evidence claims themselves. The
canonical evidence records remain owned by state/engineering-state.schema.json
and validated by portable.evidence_contract.EvidenceSpine.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Reference to a canonical evidence record at a repository snapshot."""
    evidence_id: str
    snapshot: str = ""
    freshness: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_id.strip():
            raise ValueError("evidence_id is required")

    def as_dict(self) -> dict[str, str]:
        return {"evidence_id": self.evidence_id, "snapshot": self.snapshot, "freshness": self.freshness}


@dataclass(frozen=True, slots=True)
class EngineeringEvidenceEnvelope:
    """Content-addressed lineage envelope spanning an engineering lifecycle.

    This object is immutable. bind() returns a new envelope whose
    parent_envelope_id points at the previous snapshot. Evidence claims are
    intentionally referenced by ID rather than copied into this envelope.
    """
    task_id: str
    intent_digest: str
    repository_snapshot_digest: str
    evidence: tuple[EvidenceRef, ...]
    context_plan_digest: str = ""
    decision_ids: tuple[str, ...] = ()
    changeset_id: str = ""
    verification_ids: tuple[str, ...] = ()
    review_ids: tuple[str, ...] = ()
    regression_ids: tuple[str, ...] = ()
    release_ids: tuple[str, ...] = ()
    outcome_id: str = ""
    parent_envelope_id: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    schema_version: str = SCHEMA_VERSION
    envelope_digest: str = ""

    def __post_init__(self) -> None:
        self._validate_fields()
        expected = _digest(self._canonical_payload())
        if self.envelope_digest and self.envelope_digest != expected:
            raise ValueError("envelope_digest does not match envelope contents")
        object.__setattr__(self, "envelope_digest", expected)

    def _validate_fields(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported evidence envelope schema: {self.schema_version}")
        if not self.task_id.strip():
            raise ValueError("task_id is required")
        if not self.intent_digest.strip():
            raise ValueError("intent_digest is required")
        if not self.repository_snapshot_digest.strip():
            raise ValueError("repository_snapshot_digest is required")
        if not self.evidence:
            raise ValueError("at least one evidence reference is required")
        if len({ref.evidence_id for ref in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate evidence reference")
        if any(ref.snapshot and ref.snapshot != self.repository_snapshot_digest for ref in self.evidence):
            raise ValueError("evidence reference snapshot does not match repository snapshot")
        for group_name, values in (
            ("decision_ids", self.decision_ids),
            ("verification_ids", self.verification_ids),
            ("review_ids", self.review_ids),
            ("regression_ids", self.regression_ids),
            ("release_ids", self.release_ids),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{group_name} cannot contain empty identifiers")
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate {group_name}")
        for key, value in self.metadata:
            if not key.strip():
                raise ValueError("metadata key is required")
            if not isinstance(value, str):
                raise TypeError("metadata values must be strings")

    def _canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "intent_digest": self.intent_digest,
            "repository_snapshot_digest": self.repository_snapshot_digest,
            "context_plan_digest": self.context_plan_digest,
            "evidence": [ref.as_dict() for ref in self.evidence],
            "decision_ids": list(self.decision_ids),
            "changeset_id": self.changeset_id,
            "verification_ids": list(self.verification_ids),
            "review_ids": list(self.review_ids),
            "regression_ids": list(self.regression_ids),
            "release_ids": list(self.release_ids),
            "outcome_id": self.outcome_id,
            "parent_envelope_id": self.parent_envelope_id,
            "metadata": dict(self.metadata),
        }

    def as_dict(self) -> dict[str, Any]:
        payload = self._canonical_payload()
        payload["envelope_digest"] = self.envelope_digest
        return payload

    def bind(self, *, decision_ids: Iterable[str] | None = None, changeset_id: str | None = None,
             verification_ids: Iterable[str] | None = None, review_ids: Iterable[str] | None = None,
             regression_ids: Iterable[str] | None = None, release_ids: Iterable[str] | None = None,
             outcome_id: str | None = None, metadata: Mapping[str, str] | None = None) -> "EngineeringEvidenceEnvelope":
        """Return the next immutable lifecycle snapshot."""
        return EngineeringEvidenceEnvelope(
            task_id=self.task_id, intent_digest=self.intent_digest,
            repository_snapshot_digest=self.repository_snapshot_digest, evidence=self.evidence,
            context_plan_digest=self.context_plan_digest,
            decision_ids=self.decision_ids if decision_ids is None else tuple(str(x) for x in decision_ids),
            changeset_id=self.changeset_id if changeset_id is None else str(changeset_id),
            verification_ids=self.verification_ids if verification_ids is None else tuple(str(x) for x in verification_ids),
            review_ids=self.review_ids if review_ids is None else tuple(str(x) for x in review_ids),
            regression_ids=self.regression_ids if regression_ids is None else tuple(str(x) for x in regression_ids),
            release_ids=self.release_ids if release_ids is None else tuple(str(x) for x in release_ids),
            outcome_id=self.outcome_id if outcome_id is None else str(outcome_id),
            parent_envelope_id=self.envelope_digest,
            metadata=self.metadata if metadata is None else tuple(sorted((str(k), str(v)) for k, v in metadata.items())),
        )

    def validate_against(self, spine: Any) -> None:
        """Validate every envelope reference against the canonical evidence spine."""
        if not hasattr(spine, "require"):
            raise TypeError("spine must expose require(evidence_id)")
        for ref in self.evidence:
            claim = spine.require(ref.evidence_id)
            claim_snapshot = str(getattr(claim, "snapshot", "") or "")
            if claim_snapshot and claim_snapshot != self.repository_snapshot_digest:
                raise ValueError(f"evidence snapshot mismatch: {ref.evidence_id}")

    def to_state(self) -> dict[str, Any]:
        return self.as_dict()

    @classmethod
    def from_state(cls, value: Mapping[str, Any]) -> "EngineeringEvidenceEnvelope":
        if not isinstance(value, Mapping):
            raise TypeError("evidence envelope state must be a mapping")
        raw_refs = value.get("evidence", ())
        refs = tuple(EvidenceRef(str(item.get("evidence_id", "")), str(item.get("snapshot", "")), str(item.get("freshness", "")))
                     for item in raw_refs if isinstance(item, Mapping))
        metadata = value.get("metadata", {})
        metadata_items = tuple(sorted((str(k), str(v)) for k, v in metadata.items())) if isinstance(metadata, Mapping) else ()
        return cls(
            task_id=str(value.get("task_id", "")), intent_digest=str(value.get("intent_digest", "")),
            repository_snapshot_digest=str(value.get("repository_snapshot_digest", "")), evidence=refs,
            context_plan_digest=str(value.get("context_plan_digest", "")), decision_ids=_strings(value.get("decision_ids", ())),
            changeset_id=str(value.get("changeset_id", "")), verification_ids=_strings(value.get("verification_ids", ())),
            review_ids=_strings(value.get("review_ids", ())), regression_ids=_strings(value.get("regression_ids", ())),
            release_ids=_strings(value.get("release_ids", ())), outcome_id=str(value.get("outcome_id", "")),
            parent_envelope_id=str(value.get("parent_envelope_id", "")), metadata=metadata_items,
            schema_version=str(value.get("schema_version", SCHEMA_VERSION)), envelope_digest=str(value.get("envelope_digest", "")),
        )

    @classmethod
    def from_context_evidence(cls, context_evidence: Any) -> "EngineeringEvidenceEnvelope":
        evidence_digest = str(getattr(context_evidence, "evidence_digest", "") or "")
        if not evidence_digest:
            raise ValueError("context evidence must expose evidence_digest")
        snapshot = str(getattr(context_evidence, "repository_snapshot_digest", "") or "")
        intent = str(getattr(context_evidence, "intent_digest", "") or "")
        task_id = str(getattr(context_evidence, "task_id", "") or "")
        plan = str(getattr(context_evidence, "context_plan_digest", "") or "")
        if not snapshot or not intent or not task_id:
            raise ValueError("context evidence is missing required lineage fields")
        refs = tuple(EvidenceRef(str(getattr(item, "evidence_id", "") or ""), snapshot, str(getattr(item, "freshness", "") or ""))
                     for item in getattr(context_evidence, "items", ()))
        refs = _unique_refs(refs)
        if not refs:
            refs = (EvidenceRef(evidence_digest, snapshot),)
        return cls(task_id=task_id, intent_digest=intent, repository_snapshot_digest=snapshot,
                   context_plan_digest=plan, evidence=refs, metadata=(("context_evidence_digest", evidence_digest),))


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _strings(values: Any) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        return ()
    return _unique(str(value) for value in values)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values))


def _unique_refs(values: Iterable[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    seen: set[str] = set(); result: list[EvidenceRef] = []
    for value in values:
        if value.evidence_id in seen: continue
        seen.add(value.evidence_id); result.append(value)
    return tuple(result)


__all__ = ["SCHEMA_VERSION", "EvidenceRef", "EngineeringEvidenceEnvelope"]
