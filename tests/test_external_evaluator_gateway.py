from __future__ import annotations

import json
import sys
import unittest

from portable.external_evaluator_gateway import ExternalEvaluatorCommand, ExternalEvaluatorGateway
from portable.sealed_arena_boundary import ExternalOutcomeReceipt, SealedArenaBoundary, SealedCampaignRequest, SealedCaseEnvelope
from portable.sealed_arena_exchange import export_outcome_receipt


class ExternalEvaluatorGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = SealedCampaignRequest("campaign-d", "corpus-d", (SealedCaseEnvelope("case-1", "task-d", "input-d", "env-d", True),))
        self.receipt = ExternalOutcomeReceipt("campaign-d", "corpus-d", "evaluator", "oracle", "sig", (("case-1", True, "evidence-d"),))

    def test_gateway_uses_external_process_and_sealed_receipt(self) -> None:
        payload = export_outcome_receipt(self.receipt).decode("utf-8")
        script = "import sys; sys.stdin.buffer.read(); sys.stdout.write(" + repr(payload) + ")"
        evidence = ExternalEvaluatorGateway(SealedArenaBoundary(lambda value: value.signature == "sig")).evaluate(
            self.request, ExternalEvaluatorCommand((sys.executable, "-c", script))
        )
        self.assertEqual(evidence.holdout_pass_rate, 1.0)

    def test_nonzero_exit_fails_closed(self) -> None:
        with self.assertRaises(RuntimeError):
            ExternalEvaluatorGateway(SealedArenaBoundary(lambda _value: True)).evaluate(
                self.request, ExternalEvaluatorCommand((sys.executable, "-c", "raise SystemExit(7)"))
            )

    def test_shell_is_not_used(self) -> None:
        command = ExternalEvaluatorCommand((sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"))
        self.assertEqual(command.argv[0], sys.executable)


if __name__ == "__main__":
    unittest.main()
