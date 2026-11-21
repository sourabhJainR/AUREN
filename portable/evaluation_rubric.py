"""Versioned, evidence-backed evaluation rubrics.

Rubric scoring is descriptive evidence, never execution authority. Mandatory
policy gates remain independent of weighted scores. Missing evidence is
unscored rather than zero or pass.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Mapping, Sequence


_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,95}$")


def _canonical_digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class RubricCriterion:
    criterion_id: str
    description: str
    weight: int
    evidence_required: tuple[str, ...] = ()
    mandatory: bool = False
    applicable_to: tuple[str, ...] = ()
    scoring_scale: str = "0-100"

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.criterion_id):
            raise ValueError("criterion_id must be a stable lowercase identifier")
        if not self.description.strip():
            raise ValueError("criterion description is required")
        if type(self.weight) is not int or self.weight < 0 or self.weight > 100:
            raise ValueError("criterion weight must be an integer from 0 to 100")
        if any(not isinstance(x, str) or not x.strip() for x in self.evidence_required):
            raise ValueError("evidence requirements must be non-empty strings")
        if len(set(self.evidence_required)) != len(self.evidence_required):
            raise ValueError("duplicate evidence requirement")
        if any(not isinstance(x, str) or not x.strip() for x in self.applicable_to):
            raise ValueError("applicability values must be non-empty strings")
        if type(self.mandatory) is not bool:
            raise ValueError("mandatory must be a boolean")
        if self.scoring_scale != "0-100":
            raise ValueError("only the normalized 0-100 scoring scale is supported")


@dataclass(frozen=True, slots=True)
class RubricDefinition:
    rubric_id: str
    version: str
    criteria: tuple[RubricCriterion, ...]
    release_threshold: int = 90
    hard_gate_ids: tuple[str, ...] = ()
    schema_version: str = "auren-rubric-v1"

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.rubric_id):
            raise ValueError("rubric_id must be a stable lowercase identifier")
        if not self.version.strip():
            raise ValueError("rubric version is required")
        if self.schema_version != "auren-rubric-v1":
            raise ValueError("unsupported rubric schema version")
        if not self.criteria:
            raise ValueError("at least one criterion is required")
        ids = [item.criterion_id for item in self.criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("criterion IDs must be unique")
        if sum(item.weight for item in self.criteria) != 100:
            raise ValueError("criterion weights must sum to 100")
        if type(self.release_threshold) is not int or not 0 <= self.release_threshold <= 100:
            raise ValueError("release threshold must be an integer from 0 to 100")
        if len(set(self.hard_gate_ids)) != len(self.hard_gate_ids):
            raise ValueError("hard gate IDs must be unique")
        if any(not _ID.fullmatch(item) for item in self.hard_gate_ids):
            raise ValueError("hard gate IDs must be stable lowercase identifiers")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "rubric_id": self.rubric_id,
            "version": self.version,
            "release_threshold": self.release_threshold,
            "hard_gate_ids": self.hard_gate_ids,
            "criteria": [
                {
                    "criterion_id": c.criterion_id,
                    "description": c.description,
                    "weight": c.weight,
                    "evidence_required": c.evidence_required,
                    "mandatory": c.mandatory,
                    "applicable_to": c.applicable_to,
                    "scoring_scale": c.scoring_scale,
                }
                for c in self.criteria
            ],
        }

    @property
    def digest(self) -> str:
        return _canonical_digest(self.payload())


@dataclass(frozen=True, slots=True)
class RubricScoreReceipt:
    schema_version: str
    rubric_id: str
    rubric_version: str
    rubric_digest: str
    subject_id: str
    task_ids: tuple[str, ...]
    run_id: str
    source_digest: str
    plan_digest: str
    artifact_digests: tuple[str, ...]
    executor_id: str
    evaluator_id: str
    evaluator_version: str
    mandatory_criteria: tuple[str, ...]
    criterion_scores: tuple[tuple[str, int | None], ...]
    criterion_evidence: tuple[tuple[str, tuple[str, ...]], ...]
    criterion_rationales: tuple[tuple[str, str], ...]
    criterion_applicability: tuple[tuple[str, bool], ...]
    criterion_applicability_rationales: tuple[tuple[str, str], ...]
    hard_gates: tuple[tuple[str, bool | None], ...]
    uncertainty: float
    status: str
    weighted_score: float | None
    threshold: int
    receipt_digest: str

    def __post_init__(self) -> None:
        for name in ("rubric_id", "rubric_version", "subject_id", "run_id",
                     "executor_id", "evaluator_id", "evaluator_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if self.executor_id == self.evaluator_id:
            raise ValueError("executor cannot evaluate its own candidate")
        for name in ("rubric_digest", "source_digest", "plan_digest", "receipt_digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if not self.artifact_digests or any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in self.artifact_digests
        ):
            raise ValueError("artifact digests must contain SHA-256 values")
        if any(not isinstance(item, str) or not item.strip() for item in self.task_ids) or len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("task_ids must contain unique canonical task identifiers")
        if len(set(self.mandatory_criteria)) != len(self.mandatory_criteria):
            raise ValueError("mandatory criterion IDs must be unique")
        score_map = dict(self.criterion_scores)
        evidence_map = dict(self.criterion_evidence)
        rationale_map = dict(self.criterion_rationales)
        applicability_map = dict(self.criterion_applicability)
        applicability_rationale_map = dict(self.criterion_applicability_rationales)
        gate_map = dict(self.hard_gates)
        if set(applicability_map) != set(score_map) or any(type(value) is not bool for value in applicability_map.values()):
            raise ValueError("receipt must record an explicit applicability decision for every criterion")
        if set(applicability_rationale_map) != set(score_map):
            raise ValueError("receipt must record an applicability rationale for every criterion")
        if not score_map or not set(self.mandatory_criteria).issubset(score_map):
            raise ValueError("receipt must include every declared mandatory criterion")
        if any(value is not None and (type(value) is not int or not 0 <= value <= 100) for value in score_map.values()):
            raise ValueError("criterion scores must be integers from 0 to 100 or unknown")
        if type(self.threshold) is not int or not 0 <= self.threshold <= 100:
            raise ValueError("receipt threshold must be from 0 to 100")
        complete = (
            all(
                (score_map.get(key) is not None and bool(evidence_map.get(key)) and bool(rationale_map.get(key, "").strip()))
                if applicability_map[key] else
                (score_map.get(key) is None and not evidence_map.get(key) and bool(applicability_rationale_map.get(key, "").strip()))
                for key in score_map
            )
            and all(value is not None for value in gate_map.values())
        )
        blocked = (
            any(value is False for value in gate_map.values())
            or any(score_map.get(key) != 100 for key in self.mandatory_criteria)
        )
        if self.status not in {"passed", "failed", "blocked", "unscored"}:
            raise ValueError("invalid rubric receipt status")
        if self.status == "unscored" and complete:
            raise ValueError("complete receipts cannot be marked unscored")
        if self.status == "blocked" and (not complete or not blocked):
            raise ValueError("blocked receipts require complete evidence and a failed hard gate")
        if self.status == "failed" and (
            not complete or blocked or self.weighted_score is None
            or self.weighted_score >= self.threshold
        ):
            raise ValueError("failed receipts require complete evidence, passing gates and a below-threshold score")
        if self.status == "passed" and (
            not complete or blocked or self.weighted_score is None
            or self.weighted_score < self.threshold
        ):
            raise ValueError("passed receipts require complete evidence, passing gates and threshold")
        if self.status == "unscored" and self.weighted_score is not None:
            raise ValueError("unscored receipts cannot claim a weighted score")
        if self.status != "unscored" and (
            type(self.weighted_score) not in (int, float)
            or not 0 <= self.weighted_score <= 100
        ):
            raise ValueError("scored receipts must include a weighted score from 0 to 100")
        if type(self.uncertainty) not in (int, float) or not 0 <= self.uncertainty <= 1:
            raise ValueError("receipt uncertainty must be from 0 to 1")
        expected = _canonical_digest(self.payload())
        if self.receipt_digest != expected:
            raise ValueError("receipt_digest does not match canonical receipt payload")

    def payload(self) -> dict[str, object]:
        """Return the canonical serialized payload covered by receipt_digest."""
        return {
            "schema_version": self.schema_version,
            "rubric_id": self.rubric_id,
            "rubric_version": self.rubric_version,
            "rubric_digest": self.rubric_digest,
            "subject_id": self.subject_id,
            "task_ids": list(self.task_ids),
            "run_id": self.run_id,
            "source_digest": self.source_digest,
            "plan_digest": self.plan_digest,
            "artifact_digests": list(self.artifact_digests),
            "executor_id": self.executor_id,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "mandatory_criteria": list(self.mandatory_criteria),
            "criterion_scores": dict(self.criterion_scores),
            "criterion_evidence": {k: list(v) for k, v in self.criterion_evidence},
            "criterion_rationales": dict(self.criterion_rationales),
            "criterion_applicability": dict(self.criterion_applicability),
            "criterion_applicability_rationales": dict(self.criterion_applicability_rationales),
            "hard_gates": dict(self.hard_gates),
            "uncertainty": self.uncertainty,
            "status": self.status,
            "weighted_score": self.weighted_score,
            "threshold": self.threshold,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.payload(), "receipt_digest": self.receipt_digest}


def rubric_plan_binding(rubric: RubricDefinition, *, plan_id: str, plan_digest: str, task_ids: Sequence[str]) -> dict[str, object]:
    """Return the immutable rubric pin that callers attach to a canonical plan manifest."""
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError("plan_id is required")
    if not isinstance(plan_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_digest):
        raise ValueError("plan_digest must be a lowercase SHA-256 value")
    if not task_ids or any(not isinstance(item, str) or not item.strip() for item in task_ids):
        raise ValueError("task_ids must contain non-empty canonical task IDs")
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("task_ids must be unique")
    payload = {
        "schema_version": "auren-plan-rubric-binding-v1",
        "plan_id": plan_id,
        "plan_digest": plan_digest,
        "task_ids": tuple(task_ids),
        "rubric_id": rubric.rubric_id,
        "rubric_version": rubric.version,
        "rubric_digest": rubric.digest,
    }
    return {**payload, "binding_digest": _canonical_digest(payload)}


def engineering_delivery_rubric(version: str = "1.0.0") -> RubricDefinition:
    """Return the standard software-delivery rubric profile."""
    return RubricDefinition(
        rubric_id="engineering-delivery",
        version=version,
        release_threshold=90,
        hard_gate_ids=("guard", "required_tests", "approvals", "provenance", "rollback"),
        criteria=(
            RubricCriterion("requirements", "Requirement coverage and acceptance traceability", 20, ("requirements",), True, ("all",)),
            RubricCriterion("correctness", "Behavior and implementation correctness", 20, ("tests",), True, ("all",)),
            RubricCriterion("verification", "Independent verification and regression evidence", 15, ("verification",), False, ("all",)),
            RubricCriterion("security", "Security, privacy and policy compliance", 15, ("security",), True, ("all",)),
            RubricCriterion("reliability", "Recovery, rollback and operational reliability", 10, ("reliability",), False, ("all",)),
            RubricCriterion("maintainability", "Maintainability and scope discipline", 10, ("review",), False, ("all",)),
            RubricCriterion("evidence", "Evidence completeness and provenance", 10, ("evidence",), True, ("all",)),
        ),
    )


def evaluate_rubric(
    rubric: RubricDefinition,
    *,
    subject_id: str,
    run_id: str,
    source_digest: str,
    plan_digest: str,
    evaluator_id: str,
    evaluator_version: str,
    scores: Mapping[str, int],
    evidence: Mapping[str, Sequence[str]],
    hard_gates: Mapping[str, bool | None],
    artifact_digests: Sequence[str] = (),
    rationales: Mapping[str, str] | None = None,
    applicability: Mapping[str, bool] | None = None,
    applicability_rationales: Mapping[str, str] | None = None,
    uncertainty: float = 0.0,
    executor_id: str,
    task_ids: Sequence[str] = (),
) -> RubricScoreReceipt:
    """Score a candidate against a pinned rubric and bind the result to evidence.

    A missing score or criterion evidence is unscored. False or unknown hard
    gates block passage. The executor may not act as its own evaluator.
    """
    for name, value in (
        ("subject_id", subject_id), ("run_id", run_id),
        ("source_digest", source_digest), ("plan_digest", plan_digest),
        ("executor_id", executor_id), ("evaluator_id", evaluator_id),
        ("evaluator_version", evaluator_version),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
    if not re.fullmatch(r"[0-9a-f]{64}", source_digest) or not re.fullmatch(r"[0-9a-f]{64}", plan_digest):
        raise ValueError("source_digest and plan_digest must be lowercase SHA-256 values")
    if evaluator_id == executor_id:
        raise ValueError("executor cannot evaluate its own candidate")
    if any(not isinstance(item, str) or not item.strip() for item in task_ids) or len(set(task_ids)) != len(task_ids):
        raise ValueError("task_ids must contain unique canonical task identifiers")
    if type(uncertainty) not in (int, float) or not 0.0 <= float(uncertainty) <= 1.0:
        raise ValueError("uncertainty must be a number from 0.0 to 1.0")
    if not artifact_digests:
        raise ValueError("at least one artifact digest is required")
    if any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item) for item in artifact_digests):
        raise ValueError("artifact digests must be lowercase SHA-256 values")
    if len(set(artifact_digests)) != len(artifact_digests):
        raise ValueError("artifact digests must be unique")
    rationales = rationales or {}
    applicability = applicability or {}
    applicability_rationales = applicability_rationales or {}
    known_ids = {item.criterion_id for item in rubric.criteria}
    if set(scores) - known_ids or set(evidence) - known_ids or set(rationales) - known_ids or set(applicability) - known_ids or set(applicability_rationales) - known_ids:
        raise ValueError("scores, evidence and rationales must reference known rubric criteria")
    if set(hard_gates) - set(rubric.hard_gate_ids):
        raise ValueError("unknown hard gate")
    normalized_scores: list[tuple[str, int | None]] = []
    normalized_evidence: list[tuple[str, tuple[str, ...]]] = []
    normalized_rationales: list[tuple[str, str]] = []
    normalized_applicability: list[tuple[str, bool]] = []
    normalized_applicability_rationales: list[tuple[str, str]] = []
    weighted = 0.0
    applicable_weight = 0
    complete = True
    for criterion in rubric.criteria:
        criterion_id = criterion.criterion_id
        if criterion.applicable_to and "all" not in criterion.applicable_to and criterion_id not in applicability:
            raise ValueError(f"explicit applicability decision is required for {criterion_id}")
        is_applicable = applicability.get(criterion_id, True)
        if type(is_applicable) is not bool:
            raise ValueError(f"applicability for {criterion_id} must be true or false")
        applicability_rationale = applicability_rationales.get(criterion_id, "")
        if not isinstance(applicability_rationale, str) or len(applicability_rationale) > 4000:
            raise ValueError(f"applicability rationale for {criterion_id} is invalid")
        if not is_applicable and not applicability_rationale.strip():
            complete = False
        normalized_applicability.append((criterion_id, is_applicable))
        normalized_applicability_rationales.append((criterion_id, applicability_rationale.strip()))
        if not is_applicable:
            if criterion.mandatory:
                # Mandatory criteria cannot be waived by an applicability declaration.
                raise ValueError(f"mandatory criterion {criterion_id} cannot be marked not applicable")
            if criterion_id in scores or evidence.get(criterion_id):
                raise ValueError(f"not-applicable criterion {criterion_id} cannot carry a score or evidence")
            normalized_scores.append((criterion_id, None))
            normalized_evidence.append((criterion_id, ()))
            normalized_rationales.append((criterion_id, ""))
            continue
        applicable_weight += criterion.weight
        score = scores.get(criterion_id)
        if score is not None and (type(score) is not int or not 0 <= score <= 100):
            raise ValueError(f"score for {criterion.criterion_id} must be an integer from 0 to 100")
        refs = tuple(str(x).strip() for x in evidence.get(criterion_id, ()) if str(x).strip())
        missing_kinds = [
            kind for kind in criterion.evidence_required
            if not any(ref.startswith((kind + "://", kind + ":", kind + "/")) for ref in refs)
        ]
        rationale = rationales.get(criterion_id, "")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 4000:
            complete = False
            rationale = ""
        normalized_rationales.append((criterion.criterion_id, rationale.strip()))
        if score is None or not refs or missing_kinds or not rationale.strip():
            complete = False
            normalized_scores.append((criterion.criterion_id, None))
            normalized_evidence.append((criterion.criterion_id, refs))
            continue
        normalized_scores.append((criterion.criterion_id, score))
        normalized_evidence.append((criterion.criterion_id, refs))
        weighted += criterion.weight * score / 100.0
    normalized_gates: list[tuple[str, bool | None]] = []
    gates_pass = True
    for gate_id in rubric.hard_gate_ids:
        value = hard_gates.get(gate_id)
        if value is not None and type(value) is not bool:
            raise ValueError(f"hard gate {gate_id} must be true, false or unknown")
        if value is not True:
            gates_pass = False
        normalized_gates.append((gate_id, value))
    mandatory_pass = all(
        dict(normalized_scores).get(criterion.criterion_id) == 100
        for criterion in rubric.criteria if criterion.mandatory and dict(normalized_applicability).get(criterion.criterion_id) is True
    )
    if applicable_weight <= 0:
        complete = False
    weighted = weighted * 100.0 / applicable_weight if applicable_weight else 0.0
    if not complete or any(value is None for _, value in normalized_gates):
        status = "unscored"
        weighted_score = None
    elif not gates_pass or not mandatory_pass:
        status = "blocked"
        weighted_score = round(weighted, 4)
    elif weighted < rubric.release_threshold:
        status = "failed"
        weighted_score = round(weighted, 4)
    else:
        status = "passed"
        weighted_score = round(weighted, 4)
    payload = {
        "schema_version": rubric.schema_version,
        "rubric_id": rubric.rubric_id,
        "rubric_version": rubric.version,
        "rubric_digest": rubric.digest,
        "subject_id": subject_id,
        "task_ids": list(task_ids),
        "run_id": run_id,
        "source_digest": source_digest,
        "plan_digest": plan_digest,
        "artifact_digests": list(artifact_digests),
        "executor_id": executor_id,
        "evaluator_id": evaluator_id,
        "evaluator_version": evaluator_version,
        "mandatory_criteria": [criterion.criterion_id for criterion in rubric.criteria if criterion.mandatory],
        "criterion_scores": dict(normalized_scores),
        "criterion_evidence": {key: list(value) for key, value in normalized_evidence},
        "criterion_rationales": dict(normalized_rationales),
        "criterion_applicability": dict(normalized_applicability),
        "criterion_applicability_rationales": dict(normalized_applicability_rationales),
        "hard_gates": dict(normalized_gates),
        "uncertainty": float(uncertainty),
        "status": status,
        "weighted_score": weighted_score,
        "threshold": rubric.release_threshold,
    }
    return RubricScoreReceipt(
        schema_version=rubric.schema_version,
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.version,
        rubric_digest=rubric.digest,
        subject_id=subject_id,
        task_ids=tuple(task_ids),
        run_id=run_id,
        source_digest=source_digest,
        plan_digest=plan_digest,
        artifact_digests=tuple(artifact_digests),
        executor_id=executor_id,
        evaluator_id=evaluator_id,
        evaluator_version=evaluator_version,
        mandatory_criteria=tuple(criterion.criterion_id for criterion in rubric.criteria if criterion.mandatory),
        criterion_scores=tuple(normalized_scores),
        criterion_evidence=tuple(normalized_evidence),
        criterion_rationales=tuple(normalized_rationales),
        criterion_applicability=tuple(normalized_applicability),
        criterion_applicability_rationales=tuple(normalized_applicability_rationales),
        hard_gates=tuple(normalized_gates),
        uncertainty=float(uncertainty),
        status=status,
        weighted_score=weighted_score,
        threshold=rubric.release_threshold,
        receipt_digest=_canonical_digest(payload),
    )


def rubric_receipt_projection(receipt: RubricScoreReceipt, *, task_id: str) -> dict[str, object]:
    """Return one canonical task-scoped view for CLI, MCP and operator workbench."""
    if not isinstance(receipt, RubricScoreReceipt):
        raise TypeError("receipt must be a verified RubricScoreReceipt")
    if not isinstance(task_id, str) or not task_id.strip() or task_id not in receipt.task_ids:
        raise ValueError("task_id must belong to the receipt's canonical task set")
    scores = dict(receipt.criterion_scores)
    evidence = dict(receipt.criterion_evidence)
    rationales = dict(receipt.criterion_rationales)
    applicability = dict(receipt.criterion_applicability)
    applicability_rationales = dict(receipt.criterion_applicability_rationales)
    criteria = []
    for criterion_id, applicable in applicability.items():
        criteria.append({
            "criterion_id": criterion_id,
            "status": ("not_applicable" if not applicable else
                       "scored" if scores.get(criterion_id) is not None else "unscored"),
            "applicable": applicable,
            "applicability_rationale": applicability_rationales.get(criterion_id, ""),
            "score": scores.get(criterion_id),
            "evidence": list(evidence.get(criterion_id, ())),
            "rationale": rationales.get(criterion_id, ""),
        })
    return {
        "task_id": task_id,
        "run_id": receipt.run_id,
        "subject_id": receipt.subject_id,
        "plan_digest": receipt.plan_digest,
        "rubric_id": receipt.rubric_id,
        "rubric_version": receipt.rubric_version,
        "rubric_digest": receipt.rubric_digest,
        "status": receipt.status,
        "weighted_score": receipt.weighted_score,
        "threshold": receipt.threshold,
        "hard_gates": dict(receipt.hard_gates),
        "criteria": criteria,
        "receipt_digest": receipt.receipt_digest,
    }


__all__ = [
    "RubricCriterion", "RubricDefinition", "RubricScoreReceipt",
    "evaluate_rubric", "engineering_delivery_rubric",
    "rubric_plan_binding", "persist_rubric_receipt", "rubric_receipt_projection",
]



def persist_rubric_receipt(
    ledger: object,
    receipt: RubricScoreReceipt,
    *,
    repository: str,
    head_sha: str,
) -> dict[str, str]:
    """Persist a score receipt in the existing canonical evidence ledger.

    The ledger's operator-owned storage and identity controls remain the trust
    boundary. This method does not manufacture a cryptographic evaluator
    signature or prove independent custody.
    """
    status = {
        "passed": "passed",
        "failed": "failed",
        "blocked": "failed",
        "unscored": "unknown",
    }[receipt.status]
    record = getattr(ledger, "record", None)
    if not callable(record):
        raise TypeError("ledger must expose the canonical evidence record contract")
    return record(
        evidence_id=receipt.receipt_digest,
        repository=repository,
        head_sha=head_sha,
        evidence_type="rubric_score",
        status=status,
        source_uri="urn:auren:rubric-score:" + receipt.receipt_digest,
        payload_digest=receipt.receipt_digest,
        signer_id=receipt.evaluator_id,
    )
