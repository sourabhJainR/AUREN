from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from portable.external_evaluator_gateway import ExternalEvaluatorCommand, ExternalEvaluatorGateway
from portable.sealed_arena_boundary import ExternalOutcomeReceipt, SealedArenaBoundary, SealedCampaignRequest, SealedCaseEnvelope
from portable.sealed_arena_exchange import export_outcome_receipt
from portable.sealed_arena_replay import ReplayRejected, SealedArenaReplayLedger


class ExternalEvaluatorGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.ledger = SealedArenaReplayLedger(Path(self.tempdir.name) / "replay.sqlite3")
        self.request = SealedCampaignRequest("campaign-d", "corpus-d", (SealedCaseEnvelope("case-1", "task-d", "input-d", "env-d", True),), "nonce-1")
        self.receipt = ExternalOutcomeReceipt("campaign-d", "corpus-d", "evaluator", "oracle", "sig", (("case-1", True, "evidence-d"),), run_nonce="nonce-1")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_gateway_uses_external_process_and_sealed_receipt(self) -> None:
        payload = export_outcome_receipt(self.receipt).decode("utf-8")
        script = "import sys; sys.stdin.buffer.read(); sys.stdout.write(" + repr(payload) + ")"
        evidence = ExternalEvaluatorGateway(SealedArenaBoundary(lambda value: value.signature == "sig"), self.ledger).evaluate(
            self.request, ExternalEvaluatorCommand((sys.executable, "-c", script))
        )
        self.assertEqual(evidence.holdout_pass_rate, 1.0)

    def test_nonzero_exit_fails_closed(self) -> None:
        with self.assertRaises(RuntimeError):
            ExternalEvaluatorGateway(SealedArenaBoundary(lambda _value: True), self.ledger).evaluate(
                self.request, ExternalEvaluatorCommand((sys.executable, "-c", "raise SystemExit(7)"))
            )

    def test_external_process_does_not_inherit_secrets(self) -> None:
        os.environ["AUREN_SECRET"] = "hidden"
        try:
            payload = repr(export_outcome_receipt(self.receipt).decode("utf-8"))
            script = "import os,sys; sys.stdin.buffer.read(); sys.exit(3) if \"AUREN_SECRET\" in os.environ else None; sys.stdout.buffer.write(" + payload + ".encode())"
            evidence = ExternalEvaluatorGateway(SealedArenaBoundary(lambda value: value.signature == "sig")).evaluate(
                self.request, ExternalEvaluatorCommand((sys.executable, "-c", script))
            )
            self.assertEqual(evidence.holdout_pass_rate, 1.0)
        finally:
            os.environ.pop("AUREN_SECRET", None)


    def test_replay_is_rejected_even_after_ledger_reopens(self) -> None:
        payload = export_outcome_receipt(self.receipt).decode("utf-8")
        script = "import sys; sys.stdin.buffer.read(); sys.stdout.write(" + repr(payload) + ")"
        gateway = ExternalEvaluatorGateway(SealedArenaBoundary(lambda value: value.signature == "sig"), self.ledger)
        gateway.evaluate(self.request, ExternalEvaluatorCommand((sys.executable, "-c", script)))
        reopened = SealedArenaReplayLedger(Path(self.tempdir.name) / "replay.sqlite3")
        with self.assertRaises(ReplayRejected):
            ExternalEvaluatorGateway(SealedArenaBoundary(lambda value: value.signature == "sig"), reopened).evaluate(
                self.request, ExternalEvaluatorCommand((sys.executable, "-c", script))
            )

    def test_receipt_nonce_must_match_request(self) -> None:
        wrong = ExternalOutcomeReceipt("campaign-d", "corpus-d", "evaluator", "oracle", "sig", (("case-1", True, "evidence-d"),), run_nonce="wrong-nonce")
        with self.assertRaisesRegex(ValueError, "nonce"):
            SealedArenaBoundary(lambda value: value.signature == "sig").accept(self.request, wrong)

    def test_shell_is_not_used(self) -> None:
        command = ExternalEvaluatorCommand((sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"))
        self.assertEqual(command.argv[0], sys.executable)


if __name__ == "__main__":
    unittest.main()
