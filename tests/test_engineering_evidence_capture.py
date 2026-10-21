"""Privacy and integrity regressions for automatic engineering evidence capture."""
import tempfile
import unittest
from pathlib import Path

from portable.engineering_evidence_capture import EvidenceCapture, HttpsEvidenceSink


def failing_sink(_event):
    raise RuntimeError("remote unavailable")


def succeeding_sink(_event):
    return None


class EngineeringEvidenceCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "events.sqlite3"

    def record(self, capture, **overrides):
        values = {
            "event_type": "task_attempt_started",
            "work_unit_id": "work-1",
            "task_id": "task-1",
            "iteration": 1,
            "status": "running",
            "metadata": {"handler": "unit-test", "phase": "implementation"},
        }
        values.update(overrides)
        return capture.record(**values)

    def test_events_persist_and_hash_chain_verifies_after_reopen(self):
        capture = EvidenceCapture(self.path)
        self.record(capture)
        self.record(capture, event_type="regression_failed", status="failed", iteration=2,
                    metadata={"test_suite": "unit", "failed_count": 1})
        reopened = EvidenceCapture(self.path)
        self.assertEqual(len(reopened.events()), 2)
        self.assertTrue(reopened.verify_chain())

    def test_raw_payload_or_secret_like_metadata_is_rejected(self):
        capture = EvidenceCapture(self.path)
        with self.assertRaisesRegex(ValueError, "allowlist"):
            self.record(capture, metadata={"prompt": "private task content"})
        with self.assertRaisesRegex(ValueError, "allowlist"):
            self.record(capture, metadata={"oracle_answer": "sealed answer"})

    def test_iteration_and_artifact_digests_are_validated(self):
        capture = EvidenceCapture(self.path)
        with self.assertRaises(ValueError):
            self.record(capture, iteration=-1)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.record(capture, artifact_digest="not-a-digest")

    def test_secret_like_values_and_credential_bearing_urls_are_rejected(self):
        capture = EvidenceCapture(self.path)
        with self.assertRaisesRegex(ValueError, "secret or raw task content"):
            self.record(capture, metadata={"handler": "worker password=hunter2"})
        with self.assertRaisesRegex(ValueError, "query-string secrets"):
            self.record(capture, evidence_uri="https://evidence.invalid/receipt?token=secret")
        with self.assertRaisesRegex(ValueError, "query-string secrets"):
            HttpsEvidenceSink("https://collector.invalid/events?token=secret", "token")

    def test_remote_export_is_queued_and_retriable_without_losing_local_event(self):
        capture = EvidenceCapture(self.path, sink=failing_sink)
        self.record(capture)
        self.assertEqual(capture.pending_exports(), 1)
        self.assertEqual(len(capture.events()), 1)
        capture.sink = succeeding_sink
        result = capture.flush_pending()
        self.assertEqual(result, {"sent": 1, "pending": 0})
        self.assertTrue(capture.verify_chain())

    def test_https_sink_rejects_plain_http_and_missing_token(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            HttpsEvidenceSink("http://collector.invalid/events", "token")
        with self.assertRaisesRegex(ValueError, "bearer token"):
            HttpsEvidenceSink("https://collector.invalid/events", "")

    def test_provider_operation_lifecycle_events_are_allowlisted_and_hash_chained(self):
        capture = EvidenceCapture(self.path)
        operation_id = "auren-op-" + "b" * 64
        digest = "a" * 64
        states = (
            ("provider_operation_dispatching", "dispatching"),
            ("provider_operation_reconcile_required", "reconcile_required"),
            ("provider_operation_blocked", "blocked"),
            ("provider_operation_completed", "completed"),
        )
        for event_type, state in states:
            self.record(
                capture, event_type=event_type, status="unknown",
                metadata={
                    "operation_id": operation_id, "intent_digest": digest,
                    "provider": "deploy.handler", "operation_state": state,
                },
            )
        self.assertEqual(4, len(capture.events()))
        self.assertTrue(capture.verify_chain())

    def test_signed_events_use_external_signer_without_storing_private_key(self):
        calls = []
        def signer(message):
            calls.append(message)
            return {"signer_id": "kms://tenant/key-7", "signature": "detached-signature"}
        capture = EvidenceCapture(self.path, signer=signer)
        event = self.record(capture)
        self.assertEqual(event["signer_id"], "kms://tenant/key-7")
        self.assertEqual(event["signature"], "detached-signature")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("private_key", event)


if __name__ == "__main__":
    unittest.main()
