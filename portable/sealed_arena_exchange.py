"""Cross-process exchange format for the sealed AUREN Arena boundary.

The exchange format contains only opaque digests and externally produced
outcomes. It is deliberately unsuitable for carrying benchmark answers.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from typing import Any

from .sealed_arena_boundary import ExternalOutcomeReceipt, SealedCampaignRequest

SCHEMA_VERSION = "4"


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _envelope(kind: str, payload: dict[str, Any]) -> bytes:
    body = {"schema_version": SCHEMA_VERSION, "kind": kind, "payload": payload}
    body["payload_digest"] = hashlib.sha256(_canonical(payload)).hexdigest()
    return _canonical(body)


def _decode(raw: bytes, expected_kind: str) -> dict[str, Any]:
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid sealed Arena exchange payload") from exc
    if body.get("schema_version") != SCHEMA_VERSION or body.get("kind") != expected_kind:
        raise ValueError("unsupported sealed Arena exchange envelope")
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("exchange payload must be an object")
    expected = hashlib.sha256(_canonical(payload)).hexdigest()
    if body.get("payload_digest") != expected:
        raise ValueError("sealed Arena exchange payload digest mismatch")
    return payload


def export_campaign_request(request: SealedCampaignRequest) -> bytes:
    payload = {
        "campaign_digest": request.campaign_digest,
        "corpus_digest": request.corpus_digest,
        "run_nonce": request.run_nonce,
        "cases": [case.as_dict() for case in request.cases],
        "provenance": ({
            "corpus_digest": request.provenance.corpus_digest,
            "manifest_digest": request.provenance.manifest_digest,
            "origin": request.provenance.origin,
            "split": request.provenance.split,
            "contamination_status": request.provenance.contamination_status,
            "independent_operator_id": request.provenance.independent_operator_id,
            "attestation_digest": request.provenance.attestation_digest,
        } if request.provenance else None),
        "request_digest": request.request_digest,
    }
    return _envelope("campaign_request", payload)


def import_campaign_request(raw: bytes) -> SealedCampaignRequest:
    from .sealed_arena_boundary import SealedCaseEnvelope
    payload = _decode(raw, "campaign_request")
    cases = tuple(SealedCaseEnvelope(**{**case, "transfer_dimensions": tuple(case.get("transfer_dimensions", ()))}) for case in payload.get("cases", ()))
    from .sealed_corpus_provenance import SealedCorpusProvenance
    provenance_payload = payload.get("provenance")
    provenance = SealedCorpusProvenance(**provenance_payload) if isinstance(provenance_payload, dict) else None
    request = SealedCampaignRequest(payload["campaign_digest"], payload["corpus_digest"], cases, payload.get("run_nonce", ""), provenance)
    if payload.get("request_digest") != request.request_digest:
        raise ValueError("sealed Arena request digest mismatch")
    return request


def export_outcome_receipt(receipt: ExternalOutcomeReceipt) -> bytes:
    payload = asdict(receipt)
    return _envelope("outcome_receipt", payload)


def import_outcome_receipt(raw: bytes) -> ExternalOutcomeReceipt:
    payload = _decode(raw, "outcome_receipt")
    required_freshness = {"issued_at_epoch", "expires_at_epoch"}
    if not required_freshness <= payload.keys():
        raise ValueError("outcome receipt is missing signed freshness fields")
    payload["results"] = tuple(tuple(row) for row in payload.get("results", ()))
    payload["metrics"] = tuple(tuple(row) for row in payload.get("metrics", ()))
    return ExternalOutcomeReceipt(**payload)


__all__ = ["SCHEMA_VERSION", "export_campaign_request", "import_campaign_request", "export_outcome_receipt", "import_outcome_receipt"]
