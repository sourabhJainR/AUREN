"""Fail-closed validator for externally operated Arena campaign evidence packets.

This validates packet completeness and repeat-run consistency. Independence is
only accepted when a deployment-injected trust verifier authenticates the
attestation and operator-role separation; fields in the packet cannot self-
assert trust. Repository fixtures are not external campaign evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from typing import Any

_HEX256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_FIELDS = {"run_id", "run_nonce", "result_digest", "receipt_digest", "outcome"}
_ROLE_FIELDS = {"candidate_owner", "corpus_owner", "evaluator_owner", "sandbox_auditor"}
_PACKET_FIELDS = {
    "schema_version", "campaign_id", "candidate", "source_domain", "target_domain",
    "operators", "corpus", "evaluator", "attestation", "runs", "baseline",
    "negative_controls", "resource_cost", "limitations",
}


class CampaignPacketError(ValueError):
    """Raised when a campaign packet is incomplete or internally inconsistent."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CampaignPacketError(f"{name} must be a non-empty string")
    return value.strip()


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if not _HEX256.fullmatch(value):
        raise CampaignPacketError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _object(value: Any, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise CampaignPacketError(f"{name} has unknown or missing fields")
    return value


def campaign_content_digest(packet: Mapping[str, Any]) -> str:
    """Digest all campaign content except the attestation envelope itself.

    The external signer signs this digest. Excluding the full attestation object
    avoids signature self-reference while binding the digest to every substantive
    packet field, including run receipts, corpus/evaluator identity and controls.
    """
    if not isinstance(packet, Mapping):
        raise CampaignPacketError("campaign packet must be a mapping")
    payload = {key: value for key, value in packet.items() if key != "attestation"}
    try:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise CampaignPacketError("campaign content is not canonical JSON") from None
    return hashlib.sha256(canonical).hexdigest()


def validate_campaign_packet(
    packet: Mapping[str, Any],
    *,
    verify_external_attestation: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    """Validate packet structure, trust separation, repeatability and controls.

    The verifier must use independently provisioned trust roots and authenticate
    signer, operator roles, campaign/corpus/evaluator lineage, and signatures.
    A boolean supplied inside the packet is never accepted as verification.
    """
    p = _object(packet, _PACKET_FIELDS, "campaign packet")
    if p["schema_version"] != "auren-external-campaign-packet-v1":
        raise CampaignPacketError("unsupported campaign packet schema")
    campaign_id = _text(p["campaign_id"], "campaign_id")
    _text(p["source_domain"], "source_domain")
    _text(p["target_domain"], "target_domain")
    if p["source_domain"].casefold() == p["target_domain"].casefold():
        raise CampaignPacketError("source and target domains must be distinct")

    candidate = _object(p["candidate"], {"repository", "commit_sha", "runtime_digest"}, "candidate")
    _text(candidate["repository"], "candidate.repository")
    if not isinstance(candidate["commit_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", candidate["commit_sha"]):
        raise CampaignPacketError("candidate.commit_sha must be a full lowercase Git SHA")
    _digest(candidate["runtime_digest"], "candidate.runtime_digest")

    operators = _object(p["operators"], _ROLE_FIELDS, "operators")
    role_ids = [_text(operators[name], f"operators.{name}") for name in sorted(_ROLE_FIELDS)]
    if len({value.casefold() for value in role_ids}) != len(role_ids):
        raise CampaignPacketError("candidate, corpus, evaluator and sandbox roles must have distinct operators")

    corpus = _object(p["corpus"], {"digest", "holdout_digest", "provenance_ref"}, "corpus")
    _digest(corpus["digest"], "corpus.digest")
    _digest(corpus["holdout_digest"], "corpus.holdout_digest")
    _text(corpus["provenance_ref"], "corpus.provenance_ref")
    evaluator = _object(p["evaluator"], {"id", "version", "digest", "oracle_digest"}, "evaluator")
    for key in ("id", "version"):
        _text(evaluator[key], f"evaluator.{key}")
    for key in ("digest", "oracle_digest"):
        _digest(evaluator[key], f"evaluator.{key}")

    attestation = _object(p["attestation"], {"signer_id", "key_id", "signature", "campaign_digest"}, "attestation")
    for key in ("signer_id", "key_id", "signature"):
        _text(attestation[key], f"attestation.{key}")
    declared_campaign_digest = _digest(attestation["campaign_digest"], "attestation.campaign_digest")
    expected_campaign_digest = campaign_content_digest(p)
    if declared_campaign_digest != expected_campaign_digest:
        raise CampaignPacketError("attestation campaign digest does not match canonical campaign content")
    if not callable(verify_external_attestation):
        raise CampaignPacketError("trusted external attestation verifier is required")
    try:
        verified = verify_external_attestation(p)
    except Exception:
        raise CampaignPacketError("external attestation verification failed") from None
    if verified is not True:
        raise CampaignPacketError("external attestation is not trusted")

    runs = p["runs"]
    if not isinstance(runs, list) or len(runs) != 2:
        raise CampaignPacketError("exactly two repeat runs are required")
    normalized_runs = []
    for index, raw in enumerate(runs):
        run = _object(raw, _RUN_FIELDS, f"runs[{index}]")
        for key in ("run_id", "run_nonce"):
            _text(run[key], f"runs[{index}].{key}")
        for key in ("result_digest", "receipt_digest"):
            _digest(run[key], f"runs[{index}].{key}")
        if run["outcome"] != "completed":
            raise CampaignPacketError(f"runs[{index}] did not complete")
        normalized_runs.append(dict(run))
    if normalized_runs[0]["run_id"] == normalized_runs[1]["run_id"]:
        raise CampaignPacketError("repeat runs must use distinct run identities")
    if normalized_runs[0]["run_nonce"] == normalized_runs[1]["run_nonce"]:
        raise CampaignPacketError("repeat runs must use distinct nonces")
    if normalized_runs[0]["result_digest"] != normalized_runs[1]["result_digest"]:
        raise CampaignPacketError("repeat-run result digests differ")
    if normalized_runs[0]["receipt_digest"] == normalized_runs[1]["receipt_digest"]:
        raise CampaignPacketError("repeat-run receipts must be distinct")

    baseline = _object(p["baseline"], {"method", "sample_size", "pass_rate"}, "baseline")
    _text(baseline["method"], "baseline.method")
    if type(baseline["sample_size"]) is not int or baseline["sample_size"] < 1:
        raise CampaignPacketError("baseline.sample_size must be a positive integer")
    rate = baseline["pass_rate"]
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0 <= rate <= 1:
        raise CampaignPacketError("baseline.pass_rate must be a finite probability")

    controls = p["negative_controls"]
    required_controls = {
        "nonce_replay", "receipt_tamper", "corpus_mismatch", "invalid_signature",
        "role_overlap", "missing_isolation", "partial_receipt",
    }
    if not isinstance(controls, Mapping) or set(controls) != required_controls:
        raise CampaignPacketError("negative_controls has unknown or missing controls")
    for name, raw in controls.items():
        control = _object(raw, {"outcome", "evidence_digest"}, f"negative_controls.{name}")
        if control["outcome"] != "rejected":
            raise CampaignPacketError(f"negative control {name} was not rejected")
        _digest(control["evidence_digest"], f"negative_controls.{name}.evidence_digest")

    cost = _object(p["resource_cost"], {"wall_seconds", "cpu_seconds", "peak_memory_bytes"}, "resource_cost")
    for key in ("wall_seconds", "cpu_seconds", "peak_memory_bytes"):
        value = cost[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise CampaignPacketError(f"resource_cost.{key} must be finite and non-negative")
    if not isinstance(cost["peak_memory_bytes"], int):
        raise CampaignPacketError("resource_cost.peak_memory_bytes must be an integer")

    limitations = p["limitations"]
    if not isinstance(limitations, list) or not limitations or any(not isinstance(v, str) or not v.strip() for v in limitations):
        raise CampaignPacketError("limitations must be a non-empty list of explicit statements")
    return {
        "accepted": True,
        "campaign_id": campaign_id,
        "candidate_commit": candidate["commit_sha"],
        "corpus_digest": corpus["digest"],
        "target_domain": p["target_domain"],
        "repeatability_verified": True,
        "negative_controls_rejected": sorted(required_controls),
        "limitations": list(limitations),
    }
