"""Opaque sealed Arena boundary for independently evaluated campaigns.

AUREN receives case digests and externally attested outcomes, never benchmark
answers or an in-process oracle. This module validates the trust envelope and
returns evidence; it has no promotion or execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import time
from typing import Callable, Final, Iterable, Mapping

from .sealed_corpus_provenance import SealedCorpusProvenance
from .sealed_arena_custody import verify_custody_attestation
from .sealed_arena_replay import SealedArenaReplayLedger

_FORBIDDEN_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset({
    "answer",
    "expected_answer",
    "expected_output",
    "oracle_answer",
    "oracle_output",
    "ground_truth",
    "correct_answer",
    "reference_answer",
    "answer_key",
    "gold",
    "gold_label",
    "gold_output",
    "solution",
})


def _required_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class SealedCaseEnvelope:
    case_id: str
    task_digest: str
    input_digest: str
    environment_digest: str
    holdout: bool
    transfer_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("case_id", self.case_id),
            ("task_digest", self.task_digest),
            ("input_digest", self.input_digest),
            ("environment_digest", self.environment_digest),
        ):
            _required_text(name, value)
        if type(self.holdout) is not bool:
            raise ValueError("holdout must be a boolean")
        if not isinstance(self.transfer_dimensions, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.transfer_dimensions
        ):
            raise ValueError("transfer_dimensions must be a tuple of non-empty strings")

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "task_digest": self.task_digest,
            "input_digest": self.input_digest,
            "environment_digest": self.environment_digest,
            "holdout": self.holdout,
            "transfer_dimensions": self.transfer_dimensions,
        }


@dataclass(frozen=True, slots=True)
class SealedCampaignRequest:
    campaign_digest: str
    corpus_digest: str
    cases: tuple[SealedCaseEnvelope, ...]
    run_nonce: str = ""
    provenance: SealedCorpusProvenance | None = None

    def __post_init__(self) -> None:
        _required_text("campaign_digest", self.campaign_digest)
        _required_text("corpus_digest", self.corpus_digest)
        _required_text("run_nonce", self.run_nonce)
        if not isinstance(self.cases, tuple) or not self.cases:
            raise ValueError("sealed campaign requires a non-empty tuple of cases")
        if any(not isinstance(case, SealedCaseEnvelope) for case in self.cases):
            raise ValueError("sealed campaign cases must be SealedCaseEnvelope values")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("sealed campaign contains duplicate case ids")
        if not any(case.holdout for case in self.cases):
            raise ValueError("sealed campaign requires at least one holdout")
        if self.provenance is not None and not isinstance(self.provenance, SealedCorpusProvenance):
            raise ValueError("provenance must be a SealedCorpusProvenance value")

    @property
    def manifest_digest(self) -> str:
        payload = {"corpus_digest": self.corpus_digest, "cases": [case.as_dict() for case in self.cases]}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @property
    def request_digest(self) -> str:
        """Content-address the exact case set, split assignments, nonce and lineage."""
        payload = {
            "campaign_digest": self.campaign_digest,
            "corpus_digest": self.corpus_digest,
            "run_nonce": self.run_nonce,
            "cases": [case.as_dict() for case in self.cases],
            "provenance_digest": self.provenance.provenance_digest if self.provenance else None,
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def validate_task_metadata(self, payload: Mapping[str, object]) -> None:
        forbidden: set[str] = set()

        forbidden_normalized = {
            re.sub(r"[^a-z0-9]", "", key.casefold())
            for key in _FORBIDDEN_PAYLOAD_KEYS
        }

        def inspect(value: object, path: str) -> None:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    # Normalize separators, punctuation and camelCase alike.
                    # This prevents trivial key spelling changes from bypassing
                    # the fail-closed answer-material check.
                    normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
                    if normalized in forbidden_normalized:
                        forbidden.add(f"{path}.{key}")
                    inspect(child, f"{path}.{key}")
            elif isinstance(value, (list, tuple)):
                for index, child in enumerate(value):
                    inspect(child, f"{path}[{index}]")

        inspect(payload, "payload")
        if forbidden:
            raise ValueError(
                "sealed task metadata contains benchmark answer material: "
                + ", ".join(sorted(forbidden))
            )


@dataclass(frozen=True, slots=True)
class ExternalOutcomeReceipt:
    campaign_digest: str
    corpus_digest: str
    evaluator_id: str
    oracle_id: str
    signature: str
    results: tuple[tuple[str, bool, str], ...]
    contamination_free: bool = False
    independent_oracle: bool = False
    run_nonce: str = ""
    request_digest: str = ""
    # case_id, predicted score, realized score, uncertainty, resource cost ms, recovered
    metrics: tuple[tuple[str, float, float, float, float, bool], ...] = ()
    issued_at_epoch: float = field(default_factory=time.time)
    expires_at_epoch: float = field(default_factory=lambda: time.time() + 300.0)

    def __post_init__(self) -> None:
        for name, value in (
            ("campaign_digest", self.campaign_digest),
            ("corpus_digest", self.corpus_digest),
            ("evaluator_id", self.evaluator_id),
            ("oracle_id", self.oracle_id),
            ("signature", self.signature),
            ("run_nonce", self.run_nonce),
            ("request_digest", self.request_digest),
        ):
            _required_text(name, value)
        for name, value in (("issued_at_epoch", self.issued_at_epoch), ("expires_at_epoch", self.expires_at_epoch)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive Unix timestamp")
        if self.issued_at_epoch >= self.expires_at_epoch:
            raise ValueError("receipt expiry must be later than issuance")
        if self.evaluator_id == self.oracle_id:
            raise ValueError("evaluator and oracle must be distinct principals")
        if type(self.contamination_free) is not bool:
            raise ValueError("contamination_free must be a boolean")
        if type(self.independent_oracle) is not bool:
            raise ValueError("independent_oracle must be a boolean")
        if not isinstance(self.results, tuple) or not self.results:
            raise ValueError("outcome receipt requires a non-empty tuple of results")
        for row in self.results:
            if not isinstance(row, (tuple, list)) or len(row) != 3:
                raise ValueError("outcome receipt result rows must contain case id, boolean outcome, and evidence digest")
            case_id, passed, evidence_digest = row
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError("outcome receipt contains an invalid case id")
            if type(passed) is not bool:
                raise ValueError("outcome receipt outcomes must be booleans")
            if not isinstance(evidence_digest, str) or not evidence_digest.strip():
                raise ValueError("outcome receipt contains an invalid evidence digest")
        if len({row[0] for row in self.results}) != len(self.results):
            raise ValueError("outcome receipt contains duplicate case ids")
        if not isinstance(self.metrics, tuple):
            raise ValueError("outcome receipt metrics must be a tuple")
        result_ids = {row[0] for row in self.results}
        metric_ids: set[str] = set()
        for row in self.metrics:
            if not isinstance(row, (tuple, list)) or len(row) != 6:
                raise ValueError("metric rows require case id, predicted score, realized score, uncertainty, resource cost, and recovery flag")
            case_id, predicted, realized, uncertainty, resource_cost_ms, recovered = row
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError("metric row case_id must be non-empty")
            if case_id not in result_ids:
                raise ValueError("metric row references a case without a result")
            if case_id in metric_ids:
                raise ValueError("outcome receipt contains duplicate metric case ids")
            metric_ids.add(case_id)
            for name, value in (("predicted_score", predicted), ("realized_score", realized), ("uncertainty", uncertainty)):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
                    raise ValueError(f"{name} must be finite and within [0,1]")
            if isinstance(resource_cost_ms, bool) or not isinstance(resource_cost_ms, (int, float)) or not math.isfinite(resource_cost_ms) or resource_cost_ms < 0:
                raise ValueError("resource_cost_ms must be finite and non-negative")
            if type(recovered) is not bool:
                raise ValueError("recovered must be a boolean")

    @property
    def receipt_digest(self) -> str:
        payload = {
            "campaign_digest": self.campaign_digest,
            "corpus_digest": self.corpus_digest,
            "run_nonce": self.run_nonce,
            "request_digest": self.request_digest,
            "evaluator_id": self.evaluator_id,
            "oracle_id": self.oracle_id,
            "results": self.results,
            "metrics": self.metrics,
            "contamination_free": self.contamination_free,
            "independent_oracle": self.independent_oracle,
            "issued_at_epoch": self.issued_at_epoch,
            "expires_at_epoch": self.expires_at_epoch,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class SealedArenaEvidence:
    receipt_digest: str
    holdout_pass_rate: float
    result_case_count: int
    case_results: tuple[tuple[str, bool, str], ...] = ()
    case_metrics: tuple[tuple[str, float, float, float, float, bool], ...] = ()
    request_digest: str = ""


class SealedArenaBoundary:
    """Accept externally attested outcomes without importing the oracle."""

    def __init__(self, verify_signature, *, verify_custody_signature: Callable[[str, str, bytes], bool] | None = None, replay_ledger: SealedArenaReplayLedger | None = None, clock: Callable[[], float] = time.time, max_future_skew_seconds: float = 30.0) -> None:
        if not callable(verify_signature) or not callable(clock):
            raise TypeError("signature verifier and clock must be callable")
        if verify_custody_signature is not None and not callable(verify_custody_signature):
            raise TypeError("custody signature verifier must be callable")
        if replay_ledger is not None and not isinstance(replay_ledger, SealedArenaReplayLedger):
            raise TypeError("replay_ledger must be a persistent SealedArenaReplayLedger")
        if isinstance(max_future_skew_seconds, bool) or not isinstance(max_future_skew_seconds, (int, float)) or not math.isfinite(max_future_skew_seconds) or max_future_skew_seconds < 0:
            raise ValueError("max_future_skew_seconds must be finite and non-negative")
        self._verify_signature = verify_signature
        self._verify_custody_signature = verify_custody_signature
        self._replay_ledger = replay_ledger
        self._clock = clock
        self._max_future_skew_seconds = float(max_future_skew_seconds)

    @classmethod
    def from_trusted_ed25519_keys(
        cls,
        trusted_public_keys: Mapping[str, bytes | str],
        *,
        replay_ledger: SealedArenaReplayLedger,
        revoked_signers: Iterable[str] = (),
        key_validity: Mapping[str, tuple[float, float | None]] | None = None,
        clock: Callable[[], float] = time.time,
        max_future_skew_seconds: float = 30.0,
    ) -> "SealedArenaBoundary":
        """Build a production boundary requiring signed custody, outcomes, and durable anti-replay.

        Key revocation and validity are operator-controlled trust inputs. They are
        forwarded into the actual verifier used for both custody and outcome checks,
        so production acceptance cannot accidentally bypass the key lifecycle policy.
        """
        if not isinstance(replay_ledger, SealedArenaReplayLedger):
            raise ValueError("trusted Ed25519 boundary requires a persistent replay ledger")
        from .sealed_arena_signature import Ed25519ReceiptVerifier
        verifier = Ed25519ReceiptVerifier(
            trusted_public_keys, revoked_signers=revoked_signers,
            key_validity=key_validity, clock=clock,
        )
        boundary = cls(
            verifier,
            verify_custody_signature=verifier.verify_detached,
            replay_ledger=replay_ledger,
            clock=clock,
            max_future_skew_seconds=max_future_skew_seconds,
        )
        return boundary

    @property
    def has_trusted_custody_verifier(self) -> bool:
        """Whether outcome and custody verification share the trusted Ed25519 keyring."""
        from .sealed_arena_signature import Ed25519ReceiptVerifier

        custody_verifier = self._verify_custody_signature
        return (
            isinstance(self._verify_signature, Ed25519ReceiptVerifier)
            and custody_verifier is not None
            and getattr(custody_verifier, "__self__", None) is self._verify_signature
            and getattr(custody_verifier, "__func__", None) is Ed25519ReceiptVerifier.verify_detached
        )

    def accept(self, request: SealedCampaignRequest, receipt: ExternalOutcomeReceipt) -> SealedArenaEvidence:
        return self._accept(request, receipt, replay_claimed=False)

    def _accept_preclaimed(
        self,
        request: SealedCampaignRequest,
        receipt: ExternalOutcomeReceipt,
        *,
        replay_ledger: SealedArenaReplayLedger,
    ) -> SealedArenaEvidence:
        """Accept after the gateway has claimed this nonce in the same ledger."""
        if not isinstance(replay_ledger, SealedArenaReplayLedger) or self._replay_ledger is None:
            raise ValueError("preclaimed acceptance requires the boundary's persistent replay ledger")
        from pathlib import Path
        if Path(self._replay_ledger.database_path).resolve() != Path(replay_ledger.database_path).resolve():
            raise ValueError("preclaimed acceptance requires the same replay ledger database")
        return self._accept(request, receipt, replay_claimed=True)

    def _accept(
        self,
        request: SealedCampaignRequest,
        receipt: ExternalOutcomeReceipt,
        *,
        replay_claimed: bool,
    ) -> SealedArenaEvidence:
        if not isinstance(request, SealedCampaignRequest) or not isinstance(receipt, ExternalOutcomeReceipt):
            raise TypeError("sealed request and externally attested receipt are required")
        if receipt.campaign_digest != request.campaign_digest or receipt.corpus_digest != request.corpus_digest:
            raise ValueError("outcome receipt lineage does not match sealed campaign")
        if receipt.run_nonce != request.run_nonce:
            raise ValueError("outcome receipt nonce does not match sealed campaign request")
        if receipt.request_digest != request.request_digest:
            raise ValueError("outcome receipt request digest does not match the exact sealed case set")
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError("Arena clock must return a finite Unix timestamp")
        if receipt.issued_at_epoch > now + self._max_future_skew_seconds:
            raise ValueError("external outcome receipt is issued in the future")
        if receipt.expires_at_epoch <= now:
            raise ValueError("external outcome receipt is stale or expired")
        if receipt.contamination_free is not True or receipt.independent_oracle is not True:
            raise ValueError("sealed Arena rejects contaminated or non-independent outcomes")
        if request.provenance is None:
            raise ValueError("sealed Arena rejects missing corpus provenance")
        request.provenance.validate_for_independent_gate(
            expected_corpus_digest=request.corpus_digest,
            expected_manifest_digest=request.manifest_digest,
            evaluator_id=receipt.evaluator_id,
            oracle_id=receipt.oracle_id,
        )
        if self._verify_custody_signature is not None:
            verify_custody_attestation(
                request.provenance,
                self._verify_custody_signature,
                evaluator_id=receipt.evaluator_id,
                oracle_id=receipt.oracle_id,
            )
            # In the Ed25519 production profile, the receipt signature is encoded
            # as signer-id.base64url-signature. Keep outcome attestation separate
            # from corpus custody: one trusted identity must not certify both.
            outcome_signer, separator, _encoded_signature = receipt.signature.partition(".")
            if not separator or not outcome_signer.strip():
                raise ValueError("signed outcome receipt is missing its signer identity")
            if outcome_signer == request.provenance.attestation_signer_id:
                raise ValueError("outcome signer must be independent of the holdout custody operator")
        if not self._verify_signature(receipt):
            raise ValueError("external outcome signature is not verified")
        expected_ids = {case.case_id for case in request.cases}
        observed_ids = {row[0] for row in receipt.results}
        if not observed_ids <= expected_ids:
            raise ValueError("outcome receipt contains an unknown case")
        if observed_ids != expected_ids:
            # Never disclose sealed case identifiers through exceptions or logs.
            # Report only aggregate completeness to the caller outside the evaluator.
            missing_count = len(expected_ids - observed_ids)
            raise ValueError(f"outcome receipt is incomplete; missing case count: {missing_count}")
        holdouts = {case.case_id for case in request.cases if case.holdout is True}
        holdout_results = [row for row in receipt.results if row[0] in holdouts]
        if len(holdout_results) != len(holdouts):
            raise ValueError("sealed outcome is missing one or more holdout results")
        passed = sum(row[1] is True for row in holdout_results)
        # Direct boundary callers claim only after all validation succeeds.
        # The external gateway instead claims before execution and uses the
        # preclaimed path above, so failed attempts cannot reuse their nonce.
        if self._replay_ledger is not None and not replay_claimed:
            self._replay_ledger.claim(
                run_nonce=request.run_nonce,
                campaign_digest=request.campaign_digest,
                corpus_digest=request.corpus_digest,
            )
        return SealedArenaEvidence(
            receipt.receipt_digest,
            passed / len(holdout_results),
            len(receipt.results),
            tuple(tuple(row) for row in receipt.results),
            tuple(tuple(row) for row in receipt.metrics),
            request.request_digest,
        )


__all__ = ["SealedCaseEnvelope", "SealedCampaignRequest", "ExternalOutcomeReceipt", "SealedArenaEvidence", "SealedArenaBoundary"]
