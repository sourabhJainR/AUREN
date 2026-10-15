#!/usr/bin/env python3
"""Fail-closed validator for operator-supplied AUREN production gate receipts.

This validates evidence completeness and bindings only. It does not verify
cryptographic signatures, prove evaluator independence, or run a migration or
campaign. Those claims require the named external verifier/operator artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

HEX64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ROLES = {"operator", "evaluator", "oracle", "sandbox_auditor"}
REQUIRED_MIGRATION = {
    "source_manifest_sha256", "destination_manifest_sha256", "backup_ref",
    "dry_run_ref", "apply_ref", "restart_resume_ref", "rollback_ref",
    "idempotent_rerun_ref", "operator_identity", "last_known_good_sha",
}
REQUIRED_ARENA = {
    "main_sha", "evaluator_version", "runtime_image_digest", "config_digest",
    "corpus_manifest_sha256", "custody_attestation_ref", "trust_root_version",
    "key_rotation_rehearsal_ref", "signature_verification_receipt_ref",
    "isolation_attestation_ref", "run_receipts", "negative_controls",
    "independent_review_ref", "sanitized_report_ref",
}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _digest(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        errors.append(f"{field} must be a lowercase SHA-256 hex digest")


def _distinct_roles(value: Any, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append("role_identities must be an object")
        return
    missing = REQUIRED_ROLES - set(value)
    if missing:
        errors.append("missing role identities: " + ", ".join(sorted(missing)))
        return
    identities = [value[r] for r in sorted(REQUIRED_ROLES)]
    if any(not _nonempty(x) for x in identities):
        errors.append("every role identity must be non-empty")
    elif len({x.strip() for x in identities}) != len(identities):
        errors.append("operator, evaluator, oracle, and sandbox auditor must be distinct")


def validate(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return ["top-level evidence must be a JSON object"]
    schema = payload.get("schema_version")
    if schema not in {"auren-migration-rehearsal-v1", "auren-independent-campaign-v1"}:
        return ["unsupported or missing schema_version"]
    if payload.get("external_execution") is not True:
        errors.append("external_execution must be true; local tests are not independent evidence")
    if payload.get("independent_review_passed") is not True:
        errors.append("independent_review_passed must be true")
    _distinct_roles(payload.get("role_identities"), errors)

    required = REQUIRED_MIGRATION if schema == "auren-migration-rehearsal-v1" else REQUIRED_ARENA
    for field in sorted(required):
        if field not in payload or payload[field] is None or payload[field] == "":
            errors.append(f"missing required evidence field: {field}")

    if schema == "auren-migration-rehearsal-v1":
        _digest(payload.get("source_manifest_sha256"), "source_manifest_sha256", errors)
        _digest(payload.get("destination_manifest_sha256"), "destination_manifest_sha256", errors)
        if payload.get("source_retained") is not True:
            errors.append("source_retained must be true for rollback safety")
        if payload.get("rollback_verified") is not True:
            errors.append("rollback_verified must be true")
        if payload.get("interrupted_migration_resume_verified") is not True:
            errors.append("interrupted_migration_resume_verified must be true")
    else:
        for field in ("corpus_manifest_sha256", "config_digest"):
            _digest(payload.get(field), field, errors)
        if not _nonempty(payload.get("main_sha")) or len(str(payload.get("main_sha", ""))) < 40:
            errors.append("main_sha must pin an exact commit")
        receipts = payload.get("run_receipts")
        if not isinstance(receipts, list) or len(receipts) < 2:
            errors.append("run_receipts must contain at least two fresh independently signed runs")
        else:
            nonces = [r.get("nonce") for r in receipts if isinstance(r, Mapping)]
            if len(nonces) != len(receipts) or any(not _nonempty(n) for n in nonces):
                errors.append("each run receipt must contain a nonce")
            elif len(set(nonces)) != len(nonces):
                errors.append("run receipt nonces must be distinct")
            if any(not isinstance(r, Mapping) or r.get("signature_verified") is not True for r in receipts):
                errors.append("each run receipt needs a successful external signature-verification result")
        controls = payload.get("negative_controls")
        expected = {
            "nonce_replay", "corpus_mismatch", "invalid_signature", "expired_signature",
            "role_overlap", "missing_isolation", "partial_receipt_coverage",
        }
        if not isinstance(controls, Mapping):
            errors.append("negative_controls must be an object")
        else:
            missing = expected - set(controls)
            if missing:
                errors.append("missing negative controls: " + ", ".join(sorted(missing)))
            elif any(controls[k] is not True for k in expected):
                errors.append("all required negative controls must be observed rejected")
        if payload.get("trust_verifier_mode") != "production":
            errors.append("trust_verifier_mode must be production, not a protocol/test fake")
        if payload.get("holdout_material_disclosed") is not False:
            errors.append("holdout_material_disclosed must be false")

    # Every required reference must be an opaque evidence locator, not a claim
    # copied into the manifest. The external reviewer resolves and authenticates it.
    for field in required:
        if field.endswith("_ref") and not _nonempty(payload.get(field)):
            errors.append(f"{field} must point to a retained external artifact")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="operator-supplied JSON evidence manifest")
    args = parser.parse_args()
    try:
        raw = args.evidence.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ready": False, "errors": [str(exc)]}, sort_keys=True))
        return 2
    errors = validate(payload)
    result = {
        "schema_version": payload.get("schema_version") if isinstance(payload, Mapping) else None,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "ready": not errors,
        "errors": errors,
        "scope_note": "Completeness and binding check only; external authenticity and independence must be independently verified.",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
