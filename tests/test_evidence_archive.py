"""Receiver-side archive integrity, idempotency, and signed receipt verification."""
from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

from portable.canonical_evidence_ledger import CanonicalEvidenceLedger
from portable.evidence_archive import ArchiveConflict, EvidenceArchive
from portable.engineering_evidence_capture import EvidenceCapture


class StubVerifier:
    def verify_detached(self, signer_id, signature, message):
        return signer_id == "custody-authority" and signature == "valid-signature" and bool(message)


class EvidenceArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.archive = EvidenceArchive(self.base / "archive.sqlite3", receipt_verifier=StubVerifier())

    def event(self, *, event_id="event-1", status="success"):
        capture = EvidenceCapture(self.base / (event_id.replace("/", "_") + ".sqlite3"))
        return capture.record(
            "task_completed" if status == "success" else "task_failed",
            work_unit_id="work-1", task_id=event_id, iteration=1, status=status,
            repository="owner/project", head_sha="b" * 40,
            metadata={"handler": "test-handler", "result_category": status},
            event_id=event_id,
        )

    def test_event_is_retained_idempotently_and_readable(self):
        event = self.event()
        first = self.archive.ingest(event, authenticated_principal="test-service")
        second = self.archive.ingest(event, authenticated_principal="test-service")
        self.assertEqual(first["status"], "retained")
        self.assertEqual(second["status"], "already_retained")
        stored = self.archive.get("event-1")
        self.assertEqual(stored["event"]["event_digest"], event["event_digest"])
        self.assertEqual(stored["authenticated_principal"], "test-service")

    def test_tampered_event_and_unknown_fields_are_rejected(self):
        event = self.event()
        tampered = dict(event)
        tampered["status"] = "failed"
        with self.assertRaisesRegex(ValueError, "digest verification"):
            self.archive.ingest(tampered, authenticated_principal="test-service")
        unknown = dict(event)
        unknown["raw_prompt"] = "must never be archived"
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.archive.ingest(unknown, authenticated_principal="test-service")

    def test_event_id_cannot_be_reused_for_different_content(self):
        self.archive.ingest(self.event(), authenticated_principal="test-service")
        with self.assertRaises(ArchiveConflict):
            self.archive.ingest(self.event(status="failure"), authenticated_principal="test-service")

    def test_signed_receipt_is_independently_verified_before_archive_acceptance(self):
        capture = EvidenceCapture(self.base / "runtime.sqlite3")
        payload = {
            "evidence_id": "external:receipt-1",
            "repository": "owner/project",
            "head_sha": "b" * 40,
            "evidence_type": "independent_evaluator_receipt",
            "status": "verified",
            "source_uri": "https://evidence.example.invalid/receipt-1",
            "payload_digest": "a" * 64,
            "source_type": "external_evaluator",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "run_id": "receipt-1",
            "evaluator_id": "independent-evaluator",
            "oracle_id": "sealed-oracle",
            "runtime_id": "candidate-runtime",
        }
        envelope = {"payload": payload, "signer_id": "custody-authority", "signature": "valid-signature"}
        capture.evidence_ledger = CanonicalEvidenceLedger(self.base / "canonical.sqlite3")
        capture.ingest_signed_external_evidence(
            envelope, verifier=StubVerifier(), expected_repository="owner/project",
            expected_head="b" * 40,
        )
        event = capture.events()[-1]
        event["signed_receipt"] = envelope
        result = self.archive.ingest(event, authenticated_principal="evidence-relay")
        self.assertTrue(result["receipt_signature_verified"])
        self.assertEqual(self.archive.get(event["event_id"])["receipt_digest"], event["metadata"]["receipt_digest"])


if __name__ == "__main__":
    unittest.main()
