"""Regression tests for the bounded, revision-bound evidence gate."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from portable.evidence_gate import EvidenceCheck, run_evidence_gate


class EvidenceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("/tmp/auren-evidence-gate-test")
        self.checks = (
            EvidenceCheck("unit", ("python", "-m", "pytest", "-q"), 10),
            EvidenceCheck("static", ("python", "-m", "compileall", "-q", "portable"), 10),
        )

    def test_pass_receipt_binds_expected_revision_and_has_digest(self) -> None:
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""))
        receipt = run_evidence_gate(
            self.root, expected_revision="abc123", checks=self.checks,
            command_runner=runner, revision_reader=lambda _: "abc123",
        )
        self.assertEqual(receipt.status, "PASS")
        self.assertEqual(receipt.observed_revision, "abc123")
        self.assertEqual([item.status for item in receipt.checks], ["PASS", "PASS"])
        self.assertEqual(len(receipt.digest), 64)
        self.assertFalse(runner.call_args.kwargs["shell"])

    def test_revision_change_during_checks_marks_receipt_stale(self) -> None:
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""))
        revisions = iter(("abc123", "changed-during-checks"))
        receipt = run_evidence_gate(
            self.root, expected_revision="abc123", checks=(self.checks[0],),
            command_runner=runner, revision_reader=lambda _: next(revisions),
        )
        self.assertEqual(receipt.status, "STALE")
        self.assertEqual(receipt.observed_revision, "changed-during-checks")
        self.assertEqual([item.status for item in receipt.checks], ["PASS"])
        runner.assert_called_once()

    def test_revision_read_failure_after_checks_cannot_pass(self) -> None:
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""))
        revisions = iter(("abc123", RuntimeError("repository unavailable")))
        def read_revision(_):
            value = next(revisions)
            if isinstance(value, Exception):
                raise value
            return value
        receipt = run_evidence_gate(
            self.root, expected_revision="abc123", checks=(self.checks[0],),
            command_runner=runner, revision_reader=read_revision,
        )
        self.assertEqual(receipt.status, "STALE")
        self.assertIsNone(receipt.observed_revision)

    def test_stale_revision_fails_closed_without_running_commands(self) -> None:
        runner = Mock()
        receipt = run_evidence_gate(
            self.root, expected_revision="expected", checks=self.checks,
            command_runner=runner, revision_reader=lambda _: "different",
        )
        self.assertEqual(receipt.status, "STALE")
        self.assertEqual(receipt.checks, ())
        runner.assert_not_called()

    def test_first_failed_check_blocks_later_checks(self) -> None:
        runner = Mock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="failure"))
        receipt = run_evidence_gate(
            self.root, expected_revision="abc123", checks=self.checks,
            command_runner=runner, revision_reader=lambda _: "abc123",
        )
        self.assertEqual(receipt.status, "FAIL")
        self.assertEqual([item.status for item in receipt.checks], ["FAIL", "NOT_RUN"])
        self.assertEqual(runner.call_count, 1)

    def test_timeout_is_not_reported_as_success(self) -> None:
        runner = Mock(side_effect=subprocess.TimeoutExpired(["python", "-m", "pytest"], 1))
        receipt = run_evidence_gate(
            self.root, expected_revision="abc123", checks=self.checks,
            command_runner=runner, revision_reader=lambda _: "abc123",
        )
        self.assertEqual(receipt.status, "FAIL")
        self.assertEqual(receipt.checks[0].status, "TIMEOUT")
        self.assertEqual(receipt.checks[1].status, "NOT_RUN")

    def test_default_runner_hard_stops_hung_command(self) -> None:
        started = time.monotonic()
        receipt = run_evidence_gate(
            Path.cwd(), expected_revision="abc123",
            checks=(EvidenceCheck("hang", (sys.executable, "-c", "import time; time.sleep(30)"), 10),),
            total_timeout_seconds=0.25, revision_reader=lambda _: "abc123",
        )
        self.assertEqual("FAIL", receipt.status)
        self.assertEqual("TIMEOUT", receipt.checks[0].status)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_default_runner_inherits_total_deadline_between_checks(self) -> None:
        checks = (
            EvidenceCheck("hang", (sys.executable, "-c", "import time; time.sleep(30)"), 10),
            EvidenceCheck("must-not-run", (sys.executable, "-c", "raise SystemExit(99)"), 10),
        )
        receipt = run_evidence_gate(
            Path.cwd(), expected_revision="abc123", checks=checks,
            total_timeout_seconds=0.25, revision_reader=lambda _: "abc123",
        )
        self.assertEqual("FAIL", receipt.status)
        self.assertEqual(["TIMEOUT", "NOT_RUN"], [item.status for item in receipt.checks])

    def test_pre_cancelled_evidence_gate_never_spawns_check(self) -> None:
        cancel = threading.Event()
        cancel.set()
        receipt = run_evidence_gate(
            Path.cwd(), expected_revision="abc123",
            checks=(EvidenceCheck("never", (sys.executable, "-c", "raise SystemExit(99)"), 2),),
            cancellation_event=cancel, revision_reader=lambda _: "abc123",
        )
        self.assertEqual("FAIL", receipt.status)
        self.assertEqual("CANCELLED", receipt.checks[0].status)

    def test_duplicate_check_names_are_rejected(self) -> None:
        checks = (self.checks[0], EvidenceCheck("unit", ("python", "-V")))
        with self.assertRaises(ValueError):
            run_evidence_gate(
                self.root, expected_revision="abc123", checks=checks,
                revision_reader=lambda _: "abc123",
            )

    def test_empty_check_set_cannot_claim_pass(self) -> None:
        with self.assertRaises(ValueError):
            run_evidence_gate(
                self.root, expected_revision="abc123", checks=(),
                revision_reader=lambda _: "abc123",
            )


if __name__ == "__main__":
    unittest.main()
