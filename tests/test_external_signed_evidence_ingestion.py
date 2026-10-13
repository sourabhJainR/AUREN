"""External signed receipt ingestion must bind identity, freshness, and exact head."""
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from portable.canonical_evidence_ledger import CanonicalEvidenceLedger
from portable.engineering_evidence_capture import EvidenceCapture


HEAD = "b" * 40
REPOSITORY = "owner/project"
DIGEST = "a" * 64


class StubVerifier:
    def verify_detached(self, signer_id, signature, message):
        return signer_id == "custody-authority" and signature == "valid-signature" and bool(message)


class ExternalSignedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = CanonicalEvidenceLedger(Path(self.temp.name) / "canonical.sqlite3")
        self.capture = EvidenceCapture(
            Path(self.temp.name) / "events.sqlite3", evidence_ledger=self.ledger
        )
        self.verifier = StubVerifier()

    def envelope(self, **overrides):
        payload = {
            "evidence_id": "external:run-42",
            "repository": REPOSITORY,
            "head_sha": HEAD,
            "evidence_type": "independent_evaluator_receipt",
            "status": "verified",
            "source_uri": "https://evidence.example.invalid/receipts/run-42",
            "payload_digest": DIGEST,
            "source_type": "external_evaluator",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "run_id": "run-42",
            "evaluator_id": "independent-evaluator",
            "oracle_id": "sealed-oracle",
            "runtime_id": "candidate-runtime",
            "corpus_digest": "c" * 64,
            "isolation_profile": "rootless-container",
            "external_system": "Independent Arena",
        }
        payload.update(overrides)
        return {"payload": payload, "signer_id": "custody-authority", "signature": "valid-signature"}

    def ingest(self, envelope=None):
        return self.capture.ingest_signed_external_evidence(
            envelope or self.envelope(), verifier=self.verifier,
            expected_repository=REPOSITORY, expected_head=HEAD,
        )

    def test_verified_receipt_is_retained_and_bound_to_exact_head(self):
        envelope = self.envelope()
        result = self.ingest(envelope)
        self.assertTrue(result["signature_verified"])
        self.assertEqual(len(self.capture.signed_receipts()), 1)
        self.assertEqual(self.capture.signed_receipts()[0]["envelope"], envelope)
        record = self.ledger.resolve("external:run-42", repository=REPOSITORY, head_sha=HEAD)
        self.assertEqual(record["signer_id"], "custody-authority")
        event = self.capture.events()[-1]
        self.assertNotIn("signature_verified", event["metadata"])
        self.assertEqual(event["metadata"]["receipt_digest"], result["receipt_digest"])

    def test_tampered_signature_stale_head_and_stale_timestamp_fail_closed(self):
        tampered = self.envelope()
        tampered["signature"] = "tampered"
        with self.assertRaisesRegex(ValueError, "signature verification"):
            self.ingest(tampered)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.ingest(self.envelope(head_sha="c" * 40))
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        with self.assertRaisesRegex(ValueError, "stale"):
            self.ingest(self.envelope(issued_at=old))
        self.assertEqual(self.capture.signed_receipts(), ())

    def test_evaluator_oracle_runtime_cannot_self_attest(self):
        envelope = self.envelope()
        envelope["signer_id"] = "sealed-oracle"
        with self.assertRaisesRegex(ValueError, "cannot self-attest"):
            self.ingest(envelope)

    def test_unverified_source_cannot_mark_signature_verified(self):
        with self.assertRaisesRegex(ValueError, "derived from the verified receipt store"):
            self.capture.record(
                "external_evidence_observed", work_unit_id="w", task_id="t",
                status="verified", source_type="external_evaluator",
                metadata={"signature_verified": True},
            )

    def test_operator_rollback_receipt_binds_target_and_last_known_good(self):
        envelope = self.envelope(
            source_type="operator",
            evidence_type="rollback_rehearsal",
            operator_id="release-operator",
            target_id="staging-cluster-a",
            rollback_plan_digest="d" * 64,
            last_known_good_commit="c" * 40,
            recovered_head_sha="c" * 40,
        )
        result = self.ingest(envelope)
        self.assertTrue(result["signature_verified"])
        self.assertEqual(self.ledger.resolve(
            "external:run-42", repository=REPOSITORY, head_sha=HEAD
        )["evidence_type"], "rollback_rehearsal")
        self.assertEqual(self.capture.events()[-1]["metadata"]["operator_id"], "release-operator")

    def test_successful_rollback_receipt_cannot_claim_wrong_recovered_head(self):
        envelope = self.envelope(
            source_type="operator",
            evidence_type="rollback_rehearsal",
            operator_id="release-operator",
            target_id="staging-cluster-a",
            rollback_plan_digest="d" * 64,
            last_known_good_commit="c" * 40,
            recovered_head_sha="d" * 40,
        )
        with self.assertRaisesRegex(ValueError, "last-known-good"):
            self.ingest(envelope)
        self.assertEqual(self.capture.signed_receipts(), ())

    def test_unknown_fields_and_wrong_repository_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.ingest(self.envelope(unexpected="payload"))
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.ingest(self.envelope(repository="attacker/repo"))


if __name__ == "__main__":
    unittest.main()
