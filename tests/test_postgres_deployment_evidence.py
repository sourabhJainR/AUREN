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
        "database_schema_version": "schema-2026-11",
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


def test_database_schema_version_is_required_separately_from_evidence_schema():
    payload = base("auren-postgres-admission-staging-v1")
    del payload["database_schema_version"]
    assert payload["schema_version"] == "auren-postgres-admission-staging-v1"
    assert "database_schema_version must be a non-empty deployment-bound value" in module.validate(payload)


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


def campaign_base():
    roles = {
        "operator": "campaign-operator",
        "evaluator": "external-evaluator",
        "oracle": "oracle-team",
        "sandbox_auditor": "sandbox-auditor",
        "holdout_custodian": "holdout-custodian",
        "external_signer": "external-signing-authority",
        "result_archivist": "results-archivist",
        "independent_reviewer": "independent-reviewer",
    }
    refs = {
        "custody_attestation_ref", "corpus_seal_receipt_ref",
        "external_signing_authority_ref", "trust_root_publication_ref",
        "signature_verification_receipt_ref", "isolation_attestation_ref",
        "repeated_run_receipts_ref", "negative_control_report_ref",
        "independent_result_archive_ref", "archive_retention_lock_ref",
        "independent_review_ref",
    }
    return {
        "schema_version": "auren-independent-campaign-v2",
        "campaign_id": "campaign-2026-11",
        "exact_main_sha": "a" * 40,
        "evaluator_version": "evaluator-v3",
        "runtime_image_digest": "sha256:" + "b" * 64,
        "config_digest": "c" * 64,
        "corpus_manifest_sha256": "d" * 64,
        "trust_root_version": "signer-root-v2",
        "role_identities": roles,
        "archive_admin_identity": "immutable-archive-admin",
        "run_receipts": [
            {"nonce": "run-1", "signer_id": roles["external_signer"], "signature_verified": True},
            {"nonce": "run-2", "signer_id": roles["external_signer"], "signature_verified": True},
        ],
        "negative_controls": {
            "nonce_replay": True, "corpus_mismatch": True,
            "invalid_signature": True, "expired_signature": True,
            "role_overlap": True, "missing_isolation": True,
            "partial_receipt_coverage": True,
        },
        "external_execution": True,
        "independent_review_passed": True,
        "corpus_sealed": True,
        "holdout_material_disclosed": False,
        "trust_verifier_mode": "production",
        "independent_results_retained": True,
        "archive_retention_locked": True,
        **{field: "evidence://" + field for field in refs},
    }


def test_independent_campaign_requires_separate_external_signer_and_locked_archive():
    payload = campaign_base()
    assert module.validate(payload) == []


def test_independent_campaign_rejects_signer_overlap_and_unretained_results():
    payload = campaign_base()
    payload["role_identities"]["external_signer"] = payload["role_identities"]["evaluator"]
    payload["independent_results_retained"] = False
    errors = module.validate(payload)
    assert any("must be distinct" in error for error in errors)
    assert "independent results must be retained under an immutable retention lock" in errors


def test_independent_campaign_rejects_reused_nonce_and_wrong_signer():
    payload = campaign_base()
    payload["run_receipts"][1]["nonce"] = payload["run_receipts"][0]["nonce"]
    payload["run_receipts"][1]["signer_id"] = "auren-owned-key"
    errors = module.validate(payload)
    assert "run receipt nonces must be distinct" in errors
    assert "each run receipt must bind to the independent external_signer identity" in errors
