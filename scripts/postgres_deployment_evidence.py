#!/usr/bin/env python3
"""Fail-closed completeness validator for external PostgreSQL deployment evidence.

This checks manifest structure and cross-field consistency only. It does not
authenticate referenced artifacts, verify signatures, prove physical host
independence, or connect to a production database. An independent reviewer
must resolve and authenticate every external reference.
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
SCHEMAS = {
    "auren-postgres-admission-staging-v1": {
        "refs": {
            "operator_change_ref", "deployment_config_ref", "policy_attestation_ref",
            "admission_race_ref", "restart_recovery_ref", "lease_fencing_ref",
            "outage_fail_closed_ref", "rollback_ref", "independent_review_ref",
        },
        "flags": {
            "tls_verified", "same_database_verified", "capacity_policy_match",
            "duplicate_rejected", "intent_mismatch_rejected", "restart_recovered",
            "lease_fencing_verified", "outage_failed_closed", "rollback_verified",
        },
    },
    "auren-postgres-attestation-production-v1": {
        "refs": {
            "deployment_config_ref", "trust_policy_ref", "tls_attestation_ref",
            "consistency_probe_ref", "failover_rehearsal_ref", "replay_race_ref",
            "restart_recovery_ref", "retention_policy_ref", "schema_attestation_ref",
            "independent_review_ref",
        },
        "flags": {
            "tls_verified", "same_strong_primary_verified", "consistency_verified",
            "failover_verified", "replay_race_single_winner", "restart_replay_rejected",
            "retention_policy_verified", "schema_version_match", "outage_failed_closed",
        },
    },
}
COMMON = {
    "deployment_id", "exact_main_sha", "postgres_service_id", "postgres_cluster_id",
    "postgres_primary_identity", "postgres_version", "schema_version",
    "migration_id", "policy_version", "config_digest", "host_observations",
    "role_identities", "external_execution", "independent_review_passed",
}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _digest(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        errors.append(f"{field} must be a lowercase SHA-256 hex digest")


def validate(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return ["top-level evidence must be a JSON object"]
    schema = payload.get("schema_version")
    if schema not in SCHEMAS:
        return ["unsupported or missing PostgreSQL evidence schema_version"]
    contract = SCHEMAS[schema]
    errors: list[str] = []

    for field in sorted(COMMON | contract["refs"] | contract["flags"]):
        if field not in payload or payload[field] is None or payload[field] == "":
            errors.append(f"missing required evidence field: {field}")

    if payload.get("external_execution") is not True:
        errors.append("external_execution must be true; CI and local simulations are not operator evidence")
    if payload.get("independent_review_passed") is not True:
        errors.append("independent_review_passed must be true")
    for field in ("deployment_id", "exact_main_sha", "postgres_service_id",
                  "postgres_cluster_id", "postgres_primary_identity", "postgres_version",
                  "schema_version", "migration_id", "policy_version"):
        if not _nonempty(payload.get(field)):
            errors.append(f"{field} must be a non-empty deployment-bound value")
    if not _nonempty(payload.get("exact_main_sha")) or len(str(payload.get("exact_main_sha", ""))) < 40:
        errors.append("exact_main_sha must pin an exact commit")
    _digest(payload.get("config_digest"), "config_digest", errors)

    roles = payload.get("role_identities")
    required_roles = {"operator", "database_admin", "security_reviewer", "independent_reviewer"}
    if not isinstance(roles, Mapping):
        errors.append("role_identities must identify operator, database_admin, security_reviewer, and independent_reviewer")
    else:
        missing = required_roles - set(roles)
        if missing:
            errors.append("missing role identities: " + ", ".join(sorted(missing)))
        else:
            identities = [roles[name] for name in sorted(required_roles)]
            if any(not _nonempty(value) for value in identities):
                errors.append("all role identities must be non-empty")
            elif len({value.strip() for value in identities}) != len(identities):
                errors.append("operator, database admin, security reviewer, and independent reviewer must be distinct")

    hosts = payload.get("host_observations")
    if not isinstance(hosts, list) or len(hosts) < 2:
        errors.append("host_observations must contain at least two independently operated physical hosts")
    else:
        for index, host in enumerate(hosts):
            if not isinstance(host, Mapping):
                errors.append(f"host_observations[{index}] must be an object")
                continue
            for field in ("host_id", "physical_host_id", "runtime_root_id",
                          "credential_principal", "observed_at", "config_digest",
                          "host_identity_attestation_ref"):
                if not _nonempty(host.get(field)):
                    errors.append(f"host_observations[{index}].{field} must be non-empty")
            _digest(host.get("config_digest"), f"host_observations[{index}].config_digest", errors)
        for field in ("host_id", "physical_host_id", "runtime_root_id", "credential_principal"):
            values = [h.get(field) for h in hosts if isinstance(h, Mapping)]
            if len(values) == len(hosts) and len(set(values)) != len(values):
                errors.append(f"host_observations must have distinct {field} values")
        if len([h for h in hosts if isinstance(h, Mapping)]) == len(hosts):
            if len({h.get("postgres_service_id") for h in hosts}) > 1:
                errors.append("all hosts must attest the same postgres_service_id")
            if any(h.get("postgres_service_id") != payload.get("postgres_service_id") for h in hosts):
                errors.append("each host must bind to the manifest postgres_service_id")
            if any(h.get("policy_version") != payload.get("policy_version") for h in hosts):
                errors.append("all hosts must use the manifest policy_version")

    for flag in sorted(contract["flags"]):
        if payload.get(flag) is not True:
            errors.append(f"{flag} must be true; unknown or failed outcomes remain blocked")
    for ref in sorted(contract["refs"]):
        if not _nonempty(payload.get(ref)):
            errors.append(f"{ref} must identify a retained external artifact")

    if schema == "auren-postgres-admission-staging-v1":
        for field in ("pending_capacity", "delivery_worker_capacity"):
            value = payload.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                errors.append(f"{field} must be a positive integer")
        if payload.get("rollback_mode") != "operator_controlled":
            errors.append("rollback_mode must be operator_controlled")
    else:
        for field in ("consistency_level", "failover_policy_id", "retention_policy_id",
                      "primary_epoch_before", "primary_epoch_after"):
            if not _nonempty(payload.get(field)):
                errors.append(f"{field} must be a non-empty independently attested value")
        if payload.get("consistency_level") not in {"serializable", "read_committed_primary_only"}:
            errors.append("consistency_level must name an explicitly supported primary consistency mode")
        if payload.get("primary_epoch_before") == payload.get("primary_epoch_after"):
            errors.append("failover rehearsal must record a changed primary epoch")

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
    print(json.dumps({
        "schema_version": payload.get("schema_version") if isinstance(payload, Mapping) else None,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "ready": not errors,
        "errors": errors,
        "scope_note": "Completeness only. External references, physical-host independence, signatures, and database claims require independent authentication.",
    }, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
