"""Contract tests for production_gate_evidence; fixtures are not external evidence."""
import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "production_gate_evidence.py"
spec = importlib.util.spec_from_file_location("production_gate_evidence", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProductionGateEvidenceTests(unittest.TestCase):
    def test_empty_manifest_fails_closed(self):
        errors = module.validate({})
        self.assertTrue(errors)
        self.assertIn("unsupported or missing schema_version", errors)

    def test_migration_requires_operator_rehearsal_artifacts(self):
        payload = {
            "schema_version": "auren-migration-rehearsal-v1",
            "external_execution": True,
            "independent_review_passed": True,
            "role_identities": {
                "operator": "op", "evaluator": "ev",
                "oracle": "oracle", "sandbox_auditor": "audit",
            },
        }
        errors = module.validate(payload)
        self.assertTrue(any("rollback_verified" in error for error in errors))
        self.assertTrue(any("source_retained" in error for error in errors))

    def test_role_overlap_is_rejected(self):
        payload = {
            "schema_version": "auren-migration-rehearsal-v1",
            "external_execution": True,
            "independent_review_passed": True,
            "role_identities": {
                "operator": "same", "evaluator": "same",
                "oracle": "oracle", "sandbox_auditor": "audit",
            },
        }
        self.assertIn(
            "operator, evaluator, oracle, and sandbox auditor must be distinct",
            module.validate(payload),
        )

    def test_arena_requires_two_signed_runs_and_production_verifier(self):
        payload = {
            "schema_version": "auren-independent-campaign-v1",
            "external_execution": True,
            "independent_review_passed": True,
            "role_identities": {
                "operator": "op", "evaluator": "ev",
                "oracle": "oracle", "sandbox_auditor": "audit",
            },
            "main_sha": "a" * 40,
            "evaluator_version": "v1",
            "runtime_image_digest": "sha256:" + "b" * 64,
            "config_digest": "c" * 64,
            "corpus_manifest_sha256": "d" * 64,
            "custody_attestation_ref": "external://custody",
            "trust_root_version": "root-v1",
            "key_rotation_rehearsal_ref": "external://rotation",
            "signature_verification_receipt_ref": "external://verification",
            "isolation_attestation_ref": "external://isolation",
            "run_receipts": [{"nonce": "n1", "signature_verified": True}],
            "negative_controls": {},
            "independent_review_ref": "external://review",
            "sanitized_report_ref": "external://report",
            "trust_verifier_mode": "test",
            "holdout_material_disclosed": True,
        }
        errors = module.validate(payload)
        self.assertTrue(any("at least two" in error for error in errors))
        self.assertIn("trust_verifier_mode must be production, not a protocol/test fake", errors)
        self.assertIn("holdout_material_disclosed must be false", errors)


if __name__ == "__main__":
    unittest.main()
