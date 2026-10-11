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
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _envelope(kind: str, payload: dict[str, Any]) -> bytes:
    body = {"schema_version": SCHEMA_VERSION, "kind": kind, "payload": payload}
    body["payload_digest"] = hashlib.sha256(_canonical(payload)).hexdigest()
    return _canonical(body)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _decode(raw: bytes, expected_kind: str) -> dict[str, Any]:
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid sealed Arena exchange payload") from exc
    if not isinstance(body, dict):
        raise ValueError("sealed Arena exchange envelope must be an object")
    if set(body) != {"schema_version", "kind", "payload", "payload_digest"}:
        raise ValueError("sealed Arena exchange envelope has unknown or missing fields")
    if body.get("schema_version") != SCHEMA_VERSION or body.get("kind") != expected_kind:
        raise ValueError("unsupported sealed Arena exchange envelope")
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("exchange payload must be an object")
    payload_fields = {
        "campaign_request": {
            "campaign_digest", "corpus_digest", "run_nonce", "cases", "provenance", "request_digest",
        },
        "outcome_receipt": {
            "campaign_digest", "corpus_digest", "evaluator_id", "oracle_id", "signature",
            "results", "contamination_free", "independent_oracle", "run_nonce", "request_digest",
            "metrics", "issued_at_epoch", "expires_at_epoch",
        },
    }.get(expected_kind)
    if payload_fields is None or set(payload) != payload_fields:
        raise ValueError("sealed Arena exchange payload has unknown or missing fields")
    if expected_kind == "campaign_request":
        cases = payload.get("cases")
        if not isinstance(cases, list) or any(
            not isinstance(case, dict) or set(case) != {
                "case_id", "task_digest", "input_digest", "environment_digest", "holdout", "transfer_dimensions",
            }
            for case in cases
        ):
            raise ValueError("sealed Arena request contains unknown or malformed case fields")
        provenance = payload.get("provenance")
        if provenance is not None and (
            not isinstance(provenance, dict) or set(provenance) != {
                "corpus_digest", "manifest_digest", "origin", "split", "contamination_status",
                "independent_operator_id", "attestation_digest",
            }
        ):
            raise ValueError("sealed Arena request contains unknown or malformed provenance fields")
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
