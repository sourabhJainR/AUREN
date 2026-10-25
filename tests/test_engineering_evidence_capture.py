"""Privacy and integrity regressions for automatic engineering evidence capture."""
import json
import multiprocessing
import os
import sqlite3
import tempfile
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
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
            stream.write(event["event_id"] + "\n")
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


def _flush_outbox_with_blocking_sink(database, worker_id, entered_queue, release, result_queue, delivered_log):
    def sink(event):
        with Path(delivered_log).open("a", encoding="utf-8") as stream:
            stream.write(f"{worker_id}:{event['event_id']}\n")
            stream.flush()
            os.fsync(stream.fileno())
        entered_queue.put(worker_id)
        if not release.wait(60):
            raise TimeoutError("cross-process outbox sink release timed out")

    capture = EvidenceCapture(database, sink=sink)
    result_queue.put((worker_id, capture.flush_pending()))


def _expire_outbox_claim(database):
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "UPDATE engineering_event_outbox SET lease_expires_at=? WHERE delivery_state='sending'",
            (expired,),
        )


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
                stream.write(event["event_id"] + "\n")

        reopened = EvidenceCapture(
            self.path,
            sink=successful_sink,
            evidence_ledger=CanonicalEvidenceLedger(evidence_path),
        )
        self.assertEqual(1, len(reopened.events()))
        self.assertEqual(1, len(reopened.signed_receipts()))
        self.assertEqual(1, reopened.pending_exports())
        _expire_outbox_claim(self.path)
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
        _expire_outbox_claim(self.path)

        retried_ids = []
        restarted = EvidenceCapture(self.path, sink=lambda item: retried_ids.append(item["event_id"]))
        result = restarted.flush_pending()
        self.assertEqual([event["event_id"]], retried_ids)
        self.assertEqual({"sent": 1, "pending": 0}, result)
        self.assertEqual(0, restarted.pending_exports())
        self.assertTrue(restarted.verify_chain())

    def test_concurrent_flushes_claim_a_pending_event_once(self):
        initial = EvidenceCapture(self.path, sink=failing_sink)
        event = self.record(initial)
        entered = threading.Event()
        release = threading.Event()
        calls = []
        calls_lock = threading.Lock()

        def blocking_sink(item):
            with calls_lock:
                calls.append(item["event_id"])
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test sink release timed out")

        first = EvidenceCapture(self.path, sink=blocking_sink)
        second = EvidenceCapture(self.path, sink=blocking_sink)
        first_results = []
        worker = threading.Thread(
            target=lambda: first_results.append(first.flush_pending()),
            daemon=True,
        )
        def release_worker():
            release.set()
            worker.join(5)

        self.addCleanup(release_worker)
        worker.start()
        self.assertTrue(entered.wait(5), "first flush did not claim the outbox row")
        self.assertEqual(1, first.pending_exports())
        second_result = second.flush_pending()
        self.assertEqual({"sent": 0, "pending": 1}, second_result)
        self.assertEqual([event["event_id"]], calls)

        release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "first flush did not finish")
        self.assertEqual([{"sent": 1, "pending": 0}], first_results)
        self.assertEqual(0, first.pending_exports())

    def test_concurrent_processes_claim_a_pending_event_once(self):
        initial = EvidenceCapture(self.path, sink=failing_sink)
        event = self.record(initial)
        delivered_log = self.path.parent / "cross-process-deliveries.log"
        context = multiprocessing.get_context("spawn")
        entered_queue = context.Queue()
        result_queue = context.Queue()
        release = context.Event()
        workers = [
            context.Process(
                target=_flush_outbox_with_blocking_sink,
                args=(str(self.path), worker_id, entered_queue, release, result_queue, str(delivered_log)),
            )
            for worker_id in ("worker-a", "worker-b")
        ]
        for worker in workers:
            worker.start()

        try:
            active_sink_worker = entered_queue.get(timeout=15)
            completed_worker, completed_result = result_queue.get(timeout=15)
            self.assertNotEqual(active_sink_worker, completed_worker)
            self.assertEqual({"sent": 0, "pending": 1}, completed_result)
        finally:
            release.set()
            for worker in workers:
                worker.join(15)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(3)

        self.assertTrue(
            all(worker.exitcode == 0 for worker in workers),
            f"outbox worker process failed: {[worker.exitcode for worker in workers]}",
        )
        remaining_worker, remaining_result = result_queue.get(timeout=2)
        self.assertEqual(active_sink_worker, remaining_worker)
        self.assertEqual({"sent": 1, "pending": 0}, remaining_result)
        self.assertEqual(
            [f"{active_sink_worker}:{event['event_id']}"],
            delivered_log.read_text(encoding="utf-8").splitlines(),
        )
        self.assertEqual(0, EvidenceCapture(self.path, sink=failing_sink).pending_exports())

    def test_expired_outbox_claim_is_recovered_after_crash(self):
        initial = EvidenceCapture(self.path, sink=failing_sink)
        event = self.record(initial)
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                """UPDATE engineering_event_outbox
                   SET delivery_state='sending',claim_token='dead-worker',
                       claimed_at=?,lease_expires_at=?
                   WHERE event_id=?""",
                (expired, expired, event["event_id"]),
            )

        delivered = []
        restarted = EvidenceCapture(
            self.path, sink=lambda item: delivered.append(item["event_id"])
        )
        result = restarted.flush_pending()
        self.assertEqual([event["event_id"]], delivered)
        self.assertEqual({"sent": 1, "pending": 0}, result)
        self.assertEqual(0, restarted.pending_exports())

    def test_expired_claim_cannot_finalize_over_a_newer_owner(self):
        initial = EvidenceCapture(self.path, sink=failing_sink)
        event = self.record(initial)
        entered = threading.Event()
        release = threading.Event()
        first_results = []

        def slow_sink(_item):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test sink release timed out")

        first = EvidenceCapture(self.path, sink=slow_sink)
        worker = threading.Thread(
            target=lambda: first_results.append(first.flush_pending()),
            daemon=True,
        )
        def release_worker():
            release.set()
            worker.join(5)

        self.addCleanup(release_worker)
        worker.start()
        self.assertTrue(entered.wait(5), "first flush did not claim the outbox row")

        _expire_outbox_claim(self.path)
        second_deliveries = []
        second = EvidenceCapture(
            self.path, sink=lambda item: second_deliveries.append(item["event_id"])
        )
        second_result = second.flush_pending()
        release.set()
        worker.join(5)

        self.assertFalse(worker.is_alive(), "stale claimant did not finish")
        self.assertEqual([event["event_id"]], second_deliveries)
        self.assertEqual({"sent": 1, "pending": 0}, second_result)
        self.assertEqual([{"sent": 0, "pending": 0}], first_results)
        self.assertEqual(0, second.pending_exports())

    def test_unknown_outbox_state_keeps_promotion_gate_blocked(self):
        capture = EvidenceCapture(self.path, sink=failing_sink)
        event = self.record(capture)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE engineering_event_outbox SET delivery_state='needs_operator' WHERE event_id=?",
                (event["event_id"],),
            )

        self.assertEqual(1, capture.pending_exports())
        self.assertEqual({"sent": 0, "pending": 1}, capture.flush_pending())
        self.assertEqual(1, capture.pending_exports())

    def test_existing_outbox_schema_migrates_without_losing_pending_rows(self):
        legacy_path = self.path.parent / "legacy-events.sqlite3"
        with closing(sqlite3.connect(legacy_path)) as db, db:
            db.execute(
                """CREATE TABLE engineering_event_outbox(
                    event_id TEXT PRIMARY KEY,
                    event_json TEXT NOT NULL,
                    delivery_state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """INSERT INTO engineering_event_outbox(
                    event_id,event_json,delivery_state,attempts,last_error,updated_at
                ) VALUES(?,?,?,?,?,?)""",
                ("event-legacy", '{"event_id":"event-legacy"}', "pending", 2, "URLError", "2026-01-01T00:00:00+00:00"),
            )

        EvidenceCapture(legacy_path)
        with closing(sqlite3.connect(legacy_path)) as db, db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(engineering_event_outbox)")}
            row = db.execute(
                "SELECT event_json,delivery_state,attempts,claim_token,claimed_at,lease_expires_at FROM engineering_event_outbox WHERE event_id=?",
                ("event-legacy",),
            ).fetchone()
        self.assertTrue({"claim_token", "claimed_at", "lease_expires_at"} <= columns)
        self.assertEqual(
            ('{"event_id":"event-legacy"}', "pending", 2, "", "", ""),
            row,
        )

    def test_https_sink_sends_stable_event_id_as_idempotency_key(self):
        sink = HttpsEvidenceSink("https://collector.invalid/events", "token")
        event = {"event_id": "event-123", "event_type": "task_completed", "status": "completed"}
        with patch(
            "portable.engineering_evidence_capture.guarded_https_request",
            return_value=(202, b""),
        ) as request:
            sink(event)

        self.assertEqual("https://collector.invalid/events", request.call_args.args[0])
        self.assertEqual(("collector.invalid",), request.call_args.kwargs["allowed_hosts"])
        self.assertEqual("POST", request.call_args.kwargs["method"])
        self.assertEqual("event-123", request.call_args.kwargs["headers"]["Idempotency-Key"])
        self.assertEqual("event-123", json.loads(request.call_args.kwargs["body"].decode("utf-8"))["event_id"])
        self.assertGreater(request.call_args.kwargs["timeout"], 0)


    def test_https_sink_rejects_private_destination_without_explicit_cidr_before_connect(self):
        sink = HttpsEvidenceSink("https://127.0.0.1/events", "token")
        with patch("portable.guarded_network.socket.create_connection") as connector:
            with self.assertRaisesRegex(RuntimeError, "evidence export failed"):
                sink({"event_id": "private-endpoint", "event_type": "task_completed"})
        connector.assert_not_called()

    def test_https_sink_accepts_explicit_private_cidr_scope_for_configured_endpoint(self):
        sink = HttpsEvidenceSink(
            "https://127.0.0.1/events", "token", allowed_cidrs=("127.0.0.0/8",),
        )
        with patch("portable.engineering_evidence_capture.guarded_https_request", return_value=(202, b"")) as request:
            sink({"event_id": "private-scoped", "event_type": "task_completed"})
        self.assertEqual(("127.0.0.1",), request.call_args.kwargs["allowed_hosts"])
        self.assertEqual(("127.0.0.0/8",), request.call_args.kwargs["allowed_cidrs"])

    def test_https_sink_rejects_events_without_stable_id(self):
        sink = HttpsEvidenceSink("https://collector.invalid/events", "token")
        with self.assertRaisesRegex(ValueError, "stable event_id"):
            sink({"event_type": "task_completed"})

    def test_https_sink_rejects_plain_http_and_missing_token(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            HttpsEvidenceSink("http://collector.invalid/events", "token")
        with self.assertRaisesRegex(ValueError, "bearer token"):
            HttpsEvidenceSink("https://collector.invalid/events", "")

    def test_https_sink_rejects_malformed_host_and_url_fragments(self):
        for url in (
            "https:///events",
            "https://bad host/events",
            "https://collector.invalid:bad/events",
            "https://collector.invalid:70000/events",
            "https://collector.invalid/events#fragment",
            "https://user:password@collector.invalid/events",
            "https://collector.invalid/events?token=secret",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                HttpsEvidenceSink(url, "token")

    def test_https_sink_timeout_must_be_finite_positive_number(self):
        for timeout in (True, False, 0, -1, 10**1000, float("nan"), float("inf"), float("-inf"), "5"):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "finite number"):
                HttpsEvidenceSink("https://collector.invalid/events", "token", timeout_seconds=timeout)
        self.assertEqual(
            5,
            HttpsEvidenceSink("https://collector.invalid/events", "token", timeout_seconds=5).timeout_seconds,
        )

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
