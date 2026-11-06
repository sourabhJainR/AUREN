"""Tests for external PostgreSQL evidence contracts; fixtures are not real evidence."""
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "postgres_deployment_evidence.py"
spec = importlib.util.spec_from_file_location("postgres_deployment_evidence", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def base(schema):
    refs = module.SCHEMAS[schema]["refs"]
    flags = module.SCHEMAS[schema]["flags"]
    payload = {
        "schema_version": schema,
        "deployment_id": "staging-eu-01",
        "exact_main_sha": "a" * 40,
        "postgres_service_id": "pg-service-prodlike",
        "postgres_cluster_id": "pg-cluster-01",
        "postgres_primary_identity": "pg-primary-epoch-2",
        "postgres_version": "16.4",
        "schema_version": "schema-2026-11",
        "migration_id": "migration-42",
        "policy_version": "admission-v3",
        "config_digest": "b" * 64,
        "external_execution": True,
        "independent_review_passed": True,
        "role_identities": {
            "operator": "operator-team",
            "database_admin": "db-platform-team",
            "security_reviewer": "security-team",
            "independent_reviewer": "external-reviewer",
        },
        "host_observations": [
            {
                "host_id": "host-a",
                "physical_host_id": "cloud-instance-a",
                "runtime_root_id": "root-a",
                "credential_principal": "workload-a",
                "observed_at": "2026-11-06T08:00:00Z",
                "config_digest": "c" * 64,
                "host_identity_attestation_ref": "evidence://host-a",
                "postgres_service_id": "pg-service-prodlike",
                "policy_version": "admission-v3",
            },
            {
                "host_id": "host-b",
                "physical_host_id": "cloud-instance-b",
                "runtime_root_id": "root-b",
                "credential_principal": "workload-b",
                "observed_at": "2026-11-06T08:01:00Z",
                "config_digest": "d" * 64,
                "host_identity_attestation_ref": "evidence://host-b",
                "postgres_service_id": "pg-service-prodlike",
                "policy_version": "admission-v3",
            },
        ],
    }
    payload.update({field: True for field in flags})
    payload.update({field: "evidence://" + field for field in refs})
    if schema == "auren-postgres-admission-staging-v1":
        payload.update({
            "pending_capacity": 20,
            "delivery_worker_capacity": 4,
            "rollback_mode": "operator_controlled",
        })
    else:
        payload.update({
            "consistency_level": "serializable",
            "failover_policy_id": "failover-policy-2",
            "retention_policy_id": "retention-7y",
            "primary_epoch_before": "epoch-1",
            "primary_epoch_after": "epoch-2",
        })
    return payload


def test_admission_staging_manifest_accepts_complete_distinct_host_evidence():
    payload = base("auren-postgres-admission-staging-v1")
    assert module.validate(payload) == []


def test_admission_rejects_same_physical_host_and_non_operator_rollback():
    payload = base("auren-postgres-admission-staging-v1")
    payload["host_observations"][1]["physical_host_id"] = "cloud-instance-a"
    payload["rollback_mode"] = "automatic"
    errors = module.validate(payload)
    assert any("distinct physical_host_id" in error for error in errors)
    assert "rollback_mode must be operator_controlled" in errors


def test_attestation_production_manifest_accepts_complete_failover_evidence():
    payload = base("auren-postgres-attestation-production-v1")
    assert module.validate(payload) == []


def test_attestation_rejects_same_primary_epoch_and_missing_tls_proof():
    payload = base("auren-postgres-attestation-production-v1")
    payload["primary_epoch_after"] = payload["primary_epoch_before"]
    payload["tls_verified"] = False
    errors = module.validate(payload)
    assert any("changed primary epoch" in error for error in errors)
    assert "tls_verified must be true; unknown or failed outcomes remain blocked" in errors


def test_all_external_roles_must_be_distinct():
    payload = base("auren-postgres-admission-staging-v1")
    payload["role_identities"]["independent_reviewer"] = "operator-team"
    assert any("must be distinct" in error for error in module.validate(payload))


def test_local_simulation_cannot_be_submitted_as_external_execution():
    payload = base("auren-postgres-admission-staging-v1")
    payload["external_execution"] = False
    assert "external_execution must be true; CI and local simulations are not operator evidence" in module.validate(payload)
