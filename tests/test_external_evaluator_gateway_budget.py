from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from portable.external_evaluator_gateway import ExternalEvaluatorCommand, ExternalEvaluatorGateway
from portable.sealed_arena_boundary import (
    ExternalOutcomeReceipt,
    SealedArenaBoundary,
    SealedCampaignRequest,
    SealedCaseEnvelope,
)
from portable.sealed_arena_exchange import export_outcome_receipt
from portable.sealed_arena_replay import SealedArenaReplayLedger


class ExternalEvaluatorGatewayBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.ledger = SealedArenaReplayLedger(Path(self.tempdir.name) / "replay.sqlite3")
        self.request = SealedCampaignRequest(
            "campaign-budget",
            "corpus-budget",
            (SealedCaseEnvelope("case-budget", "task-d", "input-d", "env-d", True),),
            "nonce-budget",
        )
        self.receipt = ExternalOutcomeReceipt(
            "campaign-budget",
            "corpus-budget",
            "evaluator",
            "oracle",
            "sig",
            (("case-budget", True, "evidence-d"),),
            run_nonce="nonce-budget",
            request_digest=self.request.request_digest,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_output_budget_is_enforced_during_capture(self) -> None:
        script = "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x' * 10000000)"
        gateway = ExternalEvaluatorGateway(
            SealedArenaBoundary(lambda _value: True),
            self.ledger,
        )
        with self.assertRaisesRegex(ValueError, "exceeds output budget"):
            gateway.evaluate(
                self.request,
                ExternalEvaluatorCommand(
                    (sys.executable, "-c", script),
                    timeout_seconds=5,
                    max_output_bytes=64,
                ),
            )

    def test_timeout_terminates_evaluator(self) -> None:
        script = "import sys,time; sys.stdin.buffer.read(); time.sleep(5)"
        gateway = ExternalEvaluatorGateway(
            SealedArenaBoundary(lambda _value: True),
            self.ledger,
        )
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            gateway.evaluate(
                self.request,
                ExternalEvaluatorCommand(
                    (sys.executable, "-c", script),
                    timeout_seconds=0.1,
                    max_output_bytes=1024,
                ),
            )

    def test_valid_receipt_still_passes_under_output_budget(self) -> None:
        payload = export_outcome_receipt(self.receipt).decode("utf-8")
        script = "import sys; sys.stdin.buffer.read(); sys.stdout.write(" + repr(payload) + ")"
        gateway = ExternalEvaluatorGateway(
            SealedArenaBoundary(lambda value: value.signature == "sig"),
            self.ledger,
        )
        evidence = gateway.evaluate(
            self.request,
            ExternalEvaluatorCommand(
                (sys.executable, "-c", script),
                timeout_seconds=5,
                max_output_bytes=4096,
            ),
        )
        self.assertEqual(evidence.holdout_pass_rate, 1.0)

    def test_command_rejects_invalid_budgets_and_environment_overrides(self) -> None:
        for timeout in (True, float("nan"), float("inf"), 0):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "timeout_seconds"):
                ExternalEvaluatorCommand((sys.executable, "-c", "pass"), timeout_seconds=timeout)
        for budget in (True, 0, 1.5):
            with self.subTest(budget=budget), self.assertRaisesRegex(ValueError, "max_output_bytes"):
                ExternalEvaluatorCommand((sys.executable, "-c", "pass"), max_output_bytes=budget)
        for environment in (
            (("PATH", "/tmp/untrusted"),),
            (("SAFE", "1"), ("SAFE", "2")),
            (("SAFE", 3),),
        ):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                ExternalEvaluatorCommand((sys.executable, "-c", "pass"), environment=environment)


if __name__ == "__main__":
    unittest.main()
