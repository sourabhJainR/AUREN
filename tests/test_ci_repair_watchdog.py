import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from portable.ci_repair_watchdog import CIRepairWatchdog
from portable.engineering_recovery import EngineeringRecoveryLedger


class FakeController:
    def __init__(self, path):
        self.db_path = Path(path)
        with sqlite3.connect(self.db_path) as db:
            db.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY, payload TEXT, state TEXT, heartbeat_at REAL, lease_until REAL, updated_at REAL)")
        self.created = 0

    def submit(self, handler, payload, *, goal=None):
        self.created += 1
        task_id = f"task-{self.created}"
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO tasks(id,payload,state,heartbeat_at,lease_until,updated_at) VALUES(?,?,?,?,?,?)",
                       (task_id, json.dumps(payload), "pending", 100, 200, 100))
        return SimpleNamespace(id=task_id)


class CIRepairWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.controller = FakeController(root / "controller.sqlite")
        self.recovery = EngineeringRecoveryLedger(root / "recovery.sqlite", max_same_hypothesis_attempts=2)
        self.watchdog = CIRepairWatchdog(self.controller, self.recovery)
        self.kwargs = dict(
            repository="sourabhJainR/AUREN",
            head_sha="a" * 40,
            check_identity="Production AI Harness Hardening / hardening",
            failure_fingerprint="assertion-failed-42",
            evidence_refs=["https://github.com/sourabhJainR/AUREN/actions/runs/123"],
            handler="repair_ci_failure",
            diagnosis="A required hardening assertion failed",
        )

    def test_duplicate_poll_creates_exactly_one_supervisor_task(self):
        first = self.watchdog.observe_failed_check(**self.kwargs)
        second = self.watchdog.observe_failed_check(**self.kwargs)
        self.assertEqual("queued", first["status"])
        self.assertEqual("already_queued", second["status"])
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(1, self.controller.created)

    def test_new_head_creates_distinct_task(self):
        first = self.watchdog.observe_failed_check(**self.kwargs)
        changed = dict(self.kwargs, head_sha="b" * 40)
        second = self.watchdog.observe_failed_check(**changed)
        self.assertNotEqual(first["stable_key"], second["stable_key"])
        self.assertEqual(2, self.controller.created)

    def test_evidence_refs_are_bounded_and_must_not_contain_secrets(self):
        with self.assertRaises(ValueError):
            self.watchdog.observe_failed_check(**dict(self.kwargs, evidence_refs=["https://example.test/run?token=secret"]))

    def test_repair_requires_new_evidence_or_stops_blocked(self):
        queued = self.watchdog.observe_failed_check(**self.kwargs)
        prior = self.kwargs["evidence_refs"]
        no_progress = self.watchdog.finish_repair_attempt(
            attempt_id=queued["attempt_id"], outcome="failed",
            prior_evidence_refs=prior, outcome_evidence_refs=prior,
        )
        self.assertEqual("blocked", no_progress["status"])
        self.assertIn("no new evidence", no_progress["reason"])

    def test_repair_attempt_accepts_new_evidence(self):
        queued = self.watchdog.observe_failed_check(**self.kwargs)
        result = self.watchdog.finish_repair_attempt(
            attempt_id=queued["attempt_id"], outcome="failed",
            prior_evidence_refs=self.kwargs["evidence_refs"],
            outcome_evidence_refs=self.kwargs["evidence_refs"] + [
                "https://github.com/sourabhJainR/AUREN/actions/runs/124"
            ],
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual(["https://github.com/sourabhJainR/AUREN/actions/runs/124"], result["new_evidence"])

    def test_stale_task_scan_is_observational_and_uses_existing_task_table(self):
        findings = self.watchdog.stale_tasks(stale_after_seconds=10, now=500)
        self.assertEqual([], findings)
        self.controller.submit("worker", {}, goal="stale")
        findings = self.watchdog.stale_tasks(stale_after_seconds=10, now=500)
        self.assertEqual(1, len(findings))
        self.assertEqual("stale_heartbeat_or_expired_lease", findings[0]["reason"])


if __name__ == "__main__":
    unittest.main()
