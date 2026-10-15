"""Fail-closed Arena promotion gate with a required post-promotion holdout retest.

This component returns a decision only. Guard remains the sole authority that may
activate a capability or execute rollback. External evaluator and corpus custody
must be established by deployment-owned trust roots, not local test fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import time

from .arena_run_receipt import ArenaRunReceipt
from .independent_evaluation_attestation import AttestedEvaluation
from .promotion_evidence_chain import ArenaEvidenceObservation, ArenaEvidenceObservationLedger


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class ArenaPromotionDecision:
    eligible: bool
    reasons: tuple[str, ...]
    baseline_receipt_digest: str
    retest_receipt_digest: str
    observation_digest: str
    runtime_digest: str
    configuration_digest: str
    rollback_target_commit: str
    rollback_plan_digest: str
    decision_digest: str

    def __post_init__(self) -> None:
        if type(self.eligible) is not bool:
            raise ValueError("eligible must be a boolean")
        if not isinstance(self.reasons, tuple) or any(not isinstance(x, str) or not x.strip() for x in self.reasons):
            raise ValueError("reasons must be a tuple of non-empty strings")
        if self.eligible and self.reasons:
            raise ValueError("eligible decision cannot contain rejection reasons")
        if not self.eligible and not self.reasons:
            raise ValueError("blocked decision must preserve rejection reasons")
        payload = {
            "eligible": self.eligible,
            "reasons": self.reasons,
            "baseline_receipt_digest": self.baseline_receipt_digest,
            "retest_receipt_digest": self.retest_receipt_digest,
            "observation_digest": self.observation_digest,
            "runtime_digest": self.runtime_digest,
            "configuration_digest": self.configuration_digest,
            "rollback_target_commit": self.rollback_target_commit,
            "rollback_plan_digest": self.rollback_plan_digest,
        }
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if self.decision_digest != expected:
            raise ValueError("promotion decision digest does not match its contents")


class ArenaPromotionGate:
    """Require persisted accepted baseline evidence and a fresh signed retest."""

    def evaluate(
        self,
        *,
        evidence_ledger: ArenaEvidenceObservationLedger,
        baseline_observation: ArenaEvidenceObservation,
        baseline_receipt: ArenaRunReceipt,
        post_promotion_receipt: ArenaRunReceipt,
        post_promotion_attestation: AttestedEvaluation,
        expected_runtime_digest: str,
        expected_configuration_digest: str,
        rollback_target_commit: str,
        rollback_plan_digest: str,
        now_epoch: float | None = None,
    ) -> ArenaPromotionDecision:
        reasons: list[str] = []
        now = time.time() if now_epoch is None else now_epoch
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
            reasons.append("promotion verification time is invalid")
            now = 0.0
        if not isinstance(evidence_ledger, ArenaEvidenceObservationLedger) or not evidence_ledger.database_path:
            reasons.append("durable Arena evidence ledger is required")
        if not isinstance(baseline_observation, ArenaEvidenceObservation):
            reasons.append("accepted baseline Arena observation is required")
        else:
            if not baseline_observation.accepted or baseline_observation.reasons:
                reasons.append("baseline Arena observation was rejected")
            if isinstance(evidence_ledger, ArenaEvidenceObservationLedger):
                matches = [
                    item for item in evidence_ledger.records
                    if item.run_receipt_digest == baseline_observation.run_receipt_digest
                    and item.observation_digest == baseline_observation.observation_digest
                ]
                if not matches or not any(item.accepted for item in matches):
                    reasons.append("baseline observation is not present as accepted evidence in the durable ledger")
        if not isinstance(baseline_receipt, ArenaRunReceipt):
            reasons.append("baseline Arena receipt is required")
        elif not baseline_receipt.trustworthy:
            reasons.append("baseline receipt is contaminated or untrustworthy")
        if isinstance(baseline_observation, ArenaEvidenceObservation) and isinstance(baseline_receipt, ArenaRunReceipt):
            if baseline_observation.run_receipt_digest != baseline_receipt.receipt_digest:
                reasons.append("baseline observation does not bind the supplied baseline receipt")
        if not isinstance(post_promotion_receipt, ArenaRunReceipt):
            reasons.append("post-promotion retest receipt is required")
        else:
            if not post_promotion_receipt.trustworthy:
                reasons.append("post-promotion receipt is contaminated or untrustworthy")
            if not post_promotion_receipt.campaign_digest:
                reasons.append("post-promotion receipt must bind an exact campaign")
            if isinstance(baseline_receipt, ArenaRunReceipt):
                if post_promotion_receipt.run_id == baseline_receipt.run_id:
                    reasons.append("post-promotion retest must use a new run identity")
                if post_promotion_receipt.receipt_digest == baseline_receipt.receipt_digest:
                    reasons.append("post-promotion retest must produce a new immutable receipt")
                if post_promotion_receipt.corpus_digest != baseline_receipt.corpus_digest:
                    reasons.append("post-promotion retest corpus differs from accepted baseline")
                if post_promotion_receipt.oracle_digest != baseline_receipt.oracle_digest:
                    reasons.append("post-promotion retest oracle differs from accepted baseline")
                if set(post_promotion_receipt.holdout_case_ids) != set(baseline_receipt.holdout_case_ids):
                    reasons.append("post-promotion retest holdout set differs from baseline")
            if not post_promotion_receipt.holdout_case_ids:
                reasons.append("post-promotion retest must include a holdout set")
            missing_passes = set(post_promotion_receipt.holdout_case_ids) - set(post_promotion_receipt.passed_case_ids)
            missing_verified = set(post_promotion_receipt.holdout_case_ids) - set(post_promotion_receipt.verified_case_ids)
            if missing_passes:
                reasons.append("post-promotion holdout retest contains failed cases")
            if missing_verified:
                reasons.append("post-promotion holdout retest contains unverified cases")
            if post_promotion_receipt.runtime_snapshot != expected_runtime_digest:
                reasons.append("post-promotion receipt runtime digest mismatch")
            if post_promotion_receipt.manifest_digest != expected_configuration_digest:
                reasons.append("post-promotion receipt configuration/manifest digest mismatch")
        if not isinstance(expected_runtime_digest, str) or not _SHA256.fullmatch(expected_runtime_digest):
            reasons.append("expected runtime digest must be a SHA-256 digest")
        if not isinstance(expected_configuration_digest, str) or not _SHA256.fullmatch(expected_configuration_digest):
            reasons.append("expected configuration digest must be a SHA-256 digest")
        if not isinstance(post_promotion_attestation, AttestedEvaluation):
            reasons.append("production-verified post-promotion evaluator attestation is required")
        else:
            if not post_promotion_attestation.trustworthy or not post_promotion_attestation.production_trustworthy:
                reasons.append("post-promotion attestation is not production-trustworthy and independent")
            attestation = post_promotion_attestation.attestation
            if isinstance(post_promotion_receipt, ArenaRunReceipt):
                if attestation.run_id != post_promotion_receipt.run_id:
                    reasons.append("post-promotion attestation run identity mismatch")
                if attestation.receipt_digest != post_promotion_receipt.receipt_digest:
                    reasons.append("post-promotion attestation receipt digest mismatch")
                if attestation.runtime_digest != post_promotion_receipt.runtime_snapshot:
                    reasons.append("post-promotion attestation runtime digest mismatch")
                if attestation.campaign_digest != post_promotion_receipt.campaign_digest:
                    reasons.append("post-promotion attestation campaign digest mismatch")
                if attestation.corpus_digest != post_promotion_receipt.corpus_digest:
                    reasons.append("post-promotion attestation corpus digest mismatch")
                if attestation.oracle_digest != post_promotion_receipt.oracle_digest:
                    reasons.append("post-promotion attestation oracle digest mismatch")
                if attestation.evaluator_version != post_promotion_receipt.evaluator_version:
                    reasons.append("post-promotion attestation evaluator version mismatch")
            if not attestation.issued_at_epoch or not attestation.expires_at_epoch:
                reasons.append("post-promotion attestation freshness timestamps are required")
            elif attestation.issued_at_epoch > now or attestation.expires_at_epoch <= now:
                reasons.append("post-promotion attestation is future-dated or expired")
        if not isinstance(rollback_target_commit, str) or not _COMMIT.fullmatch(rollback_target_commit):
            reasons.append("last-known-good rollback target must be a full commit SHA")
        if not isinstance(rollback_plan_digest, str) or not _SHA256.fullmatch(rollback_plan_digest):
            reasons.append("rollback plan digest must be a SHA-256 digest")

        eligible = not reasons
        baseline_digest = baseline_receipt.receipt_digest if isinstance(baseline_receipt, ArenaRunReceipt) else ""
        retest_digest = post_promotion_receipt.receipt_digest if isinstance(post_promotion_receipt, ArenaRunReceipt) else ""
        observation_digest = baseline_observation.observation_digest if isinstance(baseline_observation, ArenaEvidenceObservation) else ""
        payload = {
            "eligible": eligible, "reasons": tuple(reasons),
            "baseline_receipt_digest": baseline_digest, "retest_receipt_digest": retest_digest,
            "observation_digest": observation_digest, "runtime_digest": expected_runtime_digest if isinstance(expected_runtime_digest, str) else "",
            "configuration_digest": expected_configuration_digest if isinstance(expected_configuration_digest, str) else "",
            "rollback_target_commit": rollback_target_commit if isinstance(rollback_target_commit, str) else "",
            "rollback_plan_digest": rollback_plan_digest if isinstance(rollback_plan_digest, str) else "",
        }
        decision_digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return ArenaPromotionDecision(**payload, decision_digest=decision_digest)


__all__ = ["ArenaPromotionDecision", "ArenaPromotionGate"]
