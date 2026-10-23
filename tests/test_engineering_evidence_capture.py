"""Privacy and integrity regressions for automatic engineering evidence capture."""
import json
import multiprocessing
import os
import tempfile
from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from portable.canonical_evidence_ledger import CanonicalEvidenceLedger
from portable.engineering_evidence_capture import EvidenceCapture, HttpsEvidenceSink


def failing_sink(_event):
    raise RuntimeError("remote unavailable")


def succeeding_sink(_event):
    return None



def _crash_after_canonical_evidence_commit(evidence_db, event_db, envelope, expected_head):
    ledger = CanonicalEvidenceLedger(evidence_db)
    capture = EvidenceCapture(event_db, evidence_ledger=ledger)
    original_record = ledger.record

    def commit_then_die(**values):
        original_record(**values)
        os._exit(91)

    ledger.record = commit_then_die
    capture.ingest_signed_external_evidence(
        envelope,
        verifier=lambda _signer, _signature, _message: True,
        expected_repository="sourabhJainR/AUREN",
        expected_head=expected_head,
    )
    os._exit(92)



def _crash_after_signed_receipt_retention(evidence_db, event_db, envelope, expected_head):
    ledger = CanonicalEvidenceLedger(evidence_db)
    capture = EvidenceCapture(event_db, evidence_ledger=ledger)
    capture.record = lambda *args, **kwargs: os._exit(93)
    capture.ingest_signed_external_evidence(
        envelope,
        verifier=lambda _signer, _signature, _message: True,
        expected_repository="sourabhJainR/AUREN",
        expected_head=expected_head,
    )
    os._exit(94)


def _crash_after_signed_evidence_sink_accept(evidence_db, event_db, envelope, expected_head, accepted_log):
    def sink(event):
        with Path(accepted_log).open("a", encoding="utf-8") as stream:
            stream.write(event["event_id"] + "\\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Remote acceptance happened; the local outbox is still pending.
        os._exit(95)

    capture = EvidenceCapture(
        event_db, sink=sink, evidence_ledger=CanonicalEvidenceLedger(evidence_db)
    )
    capture.ingest_signed_external_evidence(
        envelope,
        verifier=lambda _signer, _signature, _message: True,
        expected_repository="sourabhJainR/AUREN",
        expected_head=expected_head,
    )
    os._exit(96)

def _crash_after_sink_accept(database, accepted_log):
    def sink(event):
        with Path(accepted_log).open("a", encoding="utf-8") as stream:
            stream.write(event["event_id"] + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Remote accepted the event, but the local outbox has not marked it sent.
        os._exit(89)

    capture = EvidenceCapture(database, sink=sink)
    capture.flush_pending()
    os._exit(90)


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




    def test_signed_evidence_rejects_secret_query_before_canonical_ledger_write(self):
        evidence_path = self.path.parent / "canonical-evidence.sqlite3"
        ledger = CanonicalEvidenceLedger(evidence_path)
        capture = EvidenceCapture(self.path, evidence_ledger=ledger)
        expected_head = "a" * 40
        envelope = {
            "payload": {
                "evidence_id": "external:invalid-uri:1",
                "repository": "sourabhJainR/AUREN",
                "head_sha": expected_head,
                "evidence_type": "evaluation",
                "status": "passed",
                "source_uri": "https://evaluator.invalid/receipt?token=must-not-persist",
                "payload_digest": "b" * 64,
                "source_type": "external_evaluator",
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "run_id": "run-invalid-uri",
                "evaluator_id": "evaluator-independent-1",
                "external_system": "test-evaluator",
            },
            "signer_id": "trusted-test-key-1",
            "signature": "detached-test-signature",
        }
        with self.assertRaisesRegex(ValueError, "query-string secrets"):
            capture.ingest_signed_external_evidence(
                envelope,
                verifier=lambda _signer, _signature, _message: True,
                expected_repository="sourabhJainR/AUREN",
                expected_head=expected_head,
            )
        self.assertIsNone(ledger.resolve(
            "external:invalid-uri:1",
            repository="sourabhJainR/AUREN",
            head_sha=expected_head,
        ))
        self.assertEqual((), capture.signed_receipts())
        self.assertEqual((), capture.events())

    def test_process_death_after_canonical_evidence_commit_finishes_ingestion_on_restart(self):
        evidence_path = self.path.parent / "canonical-evidence.sqlite3"
        ledger = CanonicalEvidenceLedger(evidence_path)
        capture = EvidenceCapture(self.path, evidence_ledger=ledger)
        expected_head = "a" * 40
        envelope = {
            "payload": {
                "evidence_id": "external:crash-recovery:1",
                "repository": "sourabhJainR/AUREN",
                "head_sha": expected_head,
                "evidence_type": "evaluation",
                "status": "passed",
                "source_uri": "https://evaluator.invalid/runs/42/receipt",
                "payload_digest": "b" * 64,
                "source_type": "external_evaluator",
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "run_id": "run-42",
                "evaluator_id": "evaluator-independent-1",
                "external_system": "test-evaluator",
            },
            "signer_id": "trusted-test-key-1",
            "signature": "detached-test-signature",
        }
        process = multiprocessing.get_context("spawn").Process(
            target=_crash_after_canonical_evidence_commit,
            args=(str(evidence_path), str(self.path), envelope, expected_head),
        )
        process.start()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(3)
            self.fail("signed evidence ingestion crash fixture exceeded its time budget")
        self.assertEqual(91, process.exitcode)

        reopened_ledger = CanonicalEvidenceLedger(evidence_path)
        self.assertIsNotNone(reopened_ledger.resolve(
            "external:crash-recovery:1",
            repository="sourabhJainR/AUREN",
            head_sha=expected_head,
        ))
        restarted = EvidenceCapture(self.path, evidence_ledger=reopened_ledger)
        self.assertEqual((), restarted.signed_receipts())
        self.assertEqual((), restarted.events())

        result = restarted.ingest_signed_external_evidence(
            envelope,
            verifier=lambda _signer, _signature, _message: True,
            expected_repository="sourabhJainR/AUREN",
            expected_head=expected_head,
        )
        self.assertEqual("external:crash-recovery:1", result["evidence_id"])
        self.assertEqual(1, len(restarted.signed_receipts()))
        self.assertEqual(1, len(restarted.events()))
        self.assertTrue(restarted.verify_chain())


    def test_process_death_after_signed_receipt_retention_recovers_missing_event(self):
        evidence_path = self.path.parent / "canonical-evidence.sqlite3"
        ledger = CanonicalEvidenceLedger(evidence_path)
        capture = EvidenceCapture(self.path, evidence_ledger=ledger)
        expected_head = "a" * 40
        envelope = {
            "payload": {
                "evidence_id": "external:receipt-crash-recovery:1",
                "repository": "sourabhJainR/AUREN",
                "head_sha": expected_head,
                "evidence_type": "evaluation",
                "status": "passed",
                "source_uri": "https://evaluator.invalid/runs/43/receipt",
                "payload_digest": "c" * 64,
                "source_type": "external_evaluator",
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "run_id": "run-43",
                "evaluator_id": "evaluator-independent-1",
                "external_system": "test-evaluator",
            },
            "signer_id": "trusted-test-key-1",
            "signature": "detached-test-signature",
        }
        process = multiprocessing.get_context("spawn").Process(
            target=_crash_after_signed_receipt_retention,
            args=(str(evidence_path), str(self.path), envelope, expected_head),
        )
        process.start()
        process.join(30)
        if process.is_alive():
            process.terminate()
            process.join(5)
            self.fail("signed receipt retention crash fixture exceeded its 30-second time budget")
        self.assertEqual(93, process.exitcode)

        restarted = EvidenceCapture(
            self.path, evidence_ledger=CanonicalEvidenceLedger(evidence_path)
        )
        self.assertIsNotNone(restarted.evidence_ledger.resolve(
            "external:receipt-crash-recovery:1",
            repository="sourabhJainR/AUREN",
            head_sha=expected_head,
        ))
        self.assertEqual(1, len(restarted.signed_receipts()))
        self.assertEqual((), restarted.events())
        result = restarted.ingest_signed_external_evidence(
            envelope,
            verifier=lambda _signer, _signature, _message: True,
            expected_repository="sourabhJainR/AUREN",
            expected_head=expected_head,
        )
        self.assertEqual("external:receipt-crash-recovery:1", result["evidence_id"])
        self.assertEqual(1, len(restarted.signed_receipts()))
        self.assertEqual(1, len(restarted.events()))
        self.assertTrue(restarted.verify_chain())


    def test_signed_evidence_export_crash_replays_one_stable_event_and_outbox(self):
        evidence_path = self.path.parent / "canonical-evidence.sqlite3"
        expected_head = "a" * 40
        envelope = {
            "payload": {
                "evidence_id": "external:export-crash-recovery:1",
                "repository": "sourabhJainR/AUREN",
                "head_sha": expected_head,
                "evidence_type": "evaluation",
                "status": "passed",
                "source_uri": "https://evaluator.invalid/runs/44/receipt",
                "payload_digest": "d" * 64,
                "source_type": "external_evaluator",
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "run_id": "run-44",
                "evaluator_id": "evaluator-independent-1",
                "external_system": "test-evaluator",
            },
            "signer_id": "trusted-test-key-1",
            "signature": "detached-test-signature",
        }
        accepted_log = self.path.parent / "accepted-signed-event-ids.log"
        process = multiprocessing.get_context("spawn").Process(
            target=_crash_after_signed_evidence_sink_accept,
            args=(str(evidence_path), str(self.path), envelope, expected_head, str(accepted_log)),
        )
        process.start()
        process.join(30)
        if process.is_alive():
            process.terminate()
            process.join(5)
            self.fail("signed evidence export crash fixture exceeded its 30-second time budget")
        self.assertEqual(95, process.exitcode)
        first_delivery = accepted_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(1, len(first_delivery))

        def successful_sink(event):
            with accepted_log.open("a", encoding="utf-8") as stream:
                stream.write(event["event_id"] + "\\n")

        reopened = EvidenceCapture(
            self.path,
            sink=successful_sink,
            evidence_ledger=CanonicalEvidenceLedger(evidence_path),
        )
        self.assertEqual(1, len(reopened.events()))
        self.assertEqual(1, len(reopened.signed_receipts()))
        self.assertEqual(1, reopened.pending_exports())
        result = reopened.ingest_signed_external_evidence(
            envelope,
            verifier=lambda _signer, _signature, _message: True,
            expected_repository="sourabhJainR/AUREN",
            expected_head=expected_head,
        )
        self.assertEqual("external:export-crash-recovery:1", result["evidence_id"])
        self.assertEqual(1, len(reopened.events()))
        self.assertEqual(1, len(reopened.signed_receipts()))
        self.assertEqual(0, reopened.pending_exports())
        deliveries = accepted_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(2, len(deliveries))
        self.assertEqual(deliveries[0], deliveries[1])
        self.assertTrue(reopened.verify_chain())

    def test_process_death_after_sink_accept_replays_same_stable_event_id(self):
        # Configure a failing sink first so the local outbox has a pending row.
        capture = EvidenceCapture(self.path, sink=failing_sink)
        event = self.record(capture)
        accepted_log = self.path.parent / "accepted-event-ids.log"
        process = multiprocessing.get_context("spawn").Process(
            target=_crash_after_sink_accept,
            args=(str(self.path), str(accepted_log)),
        )
        process.start()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(3)
            self.fail("evidence sink crash fixture exceeded its time budget")
        self.assertEqual(89, process.exitcode)
        self.assertEqual(1, capture.pending_exports())
        first_delivery = accepted_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual([event["event_id"]], first_delivery)

        retried_ids = []
        restarted = EvidenceCapture(self.path, sink=lambda item: retried_ids.append(item["event_id"]))
        result = restarted.flush_pending()
        self.assertEqual([event["event_id"]], retried_ids)
        self.assertEqual({"sent": 1, "pending": 0}, result)
        self.assertEqual(0, restarted.pending_exports())
        self.assertTrue(restarted.verify_chain())

    def test_https_sink_sends_stable_event_id_as_idempotency_key(self):
        sink = HttpsEvidenceSink("https://collector.invalid/events", "token")
        response = MagicMock()
        response.status = 202
        response.__enter__.return_value = response
        captured = []

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return response

        event = {"event_id": "event-123", "event_type": "task_completed", "status": "completed"}
        with patch("portable.engineering_evidence_capture.urlopen", side_effect=fake_urlopen):
            sink(event)

        request, timeout = captured[0]
        self.assertEqual("event-123", request.get_header("Idempotency-key"))
        self.assertEqual("event-123", json.loads(request.data.decode("utf-8"))["event_id"])
        self.assertGreater(timeout, 0)

    def test_https_sink_rejects_events_without_stable_id(self):
        sink = HttpsEvidenceSink("https://collector.invalid/events", "token")
        with self.assertRaisesRegex(ValueError, "stable event_id"):
            sink({"event_type": "task_completed"})

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
