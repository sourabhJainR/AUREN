"""Regression tests for the bounded, revision-bound evidence gate."""
from __future__ import annotations

import subprocess
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

    def test_duplicate_check_names_are_rejected(self) -> None:
        checks = (self.checks[0], EvidenceCheck("unit", ("python", "-V")))
        with self.assertRaises(ValueError):
            run_evidence_gate(
                self.root, expected_revision="abc123", checks=checks,
                revision_reader=lambda _: "abc123",
            )

    def test_empty_check_set_cannot_claim_pass(self) -> None:
        receipt = run_evidence_gate(
            self.root, expected_revision="abc123", checks=(),
            revision_reader=lambda _: "abc123",
        )
        self.assertEqual(receipt.status, "FAIL")


if __name__ == "__main__":
    unittest.main()
