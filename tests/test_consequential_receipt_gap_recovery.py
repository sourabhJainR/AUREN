"""Regression for recovery when the child receipt survives a progress-write failure."""
import tempfile
import unittest
from pathlib import Path

from portable.execution_controller import ExecutionController
from portable.side_effect_ledger import DurableSideEffectLedger


_CALLS = []


def _context_fixture(_payload):
    return {"trusted": True}


def _ci_fixture(_payload):
    return {"status": "completed", "conclusion": "success"}


def _action_fixture(payload):
    _CALLS.append(payload["revision"])
    return {"success": True, "detail": "test fixture result"}


class ConsequentialReceiptGapRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        _CALLS.clear()
        self.root = Path(self.temp.name)
        self.controller = ExecutionController(self.root)
        self._register(self.controller)

    @staticmethod
    def _register(controller):
        controller.register_handler(
            "receipt-gap-context-fixture", _context_fixture, execution_class="context_provider",
        )
        controller.register_handler(
            "receipt-gap-ci-fixture", _ci_fixture, execution_class="context_provider",
        )
        controller.register_handler(
            "receipt-gap-action-fixture", _action_fixture,
            promotion_gate_policy={"context_handler": "receipt-gap-context-fixture"},
            ci_gate_policy={
                "expected_head_sha": "a" * 40,
                "required_checks": ["required-ci"],
                "evidence_handler": "receipt-gap-ci-fixture",
            },
            execution_class="consequential",
        )
        controller._preflight_gates = lambda task: (True, "test fixture gates passed", [])

    def test_completed_child_receipt_repairs_outer_record_without_second_dispatch(self):
        payload = {
            "revision": "fixture-revision",
            "auren_idempotency": {"key": "receipt-gap:fixture-operation"},
        }
        first = self.controller.submit("receipt-gap-action-fixture", payload)
        original_progress = self.controller._record_progress

        def fail_after_child_receipt(*args, **kwargs):
            event = args[1] if len(args) > 1 else kwargs.get("event")
            if event == "provider_operation_completed":
                raise OSError("injected progress persistence failure after child receipt")
            return original_progress(*args, **kwargs)

        self.controller._record_progress = fail_after_child_receipt
        try:
            self.controller.run_once(first.id)
        finally:
            self.controller._record_progress = original_progress

        self.assertEqual("failed", self.controller.task(first.id).state)
        self.assertEqual(["fixture-revision"], _CALLS)
        outer_key = "consequential-idempotency:receipt-gap:fixture-operation"
        inner_key = "provider-operation:" + outer_key
        ledger = DurableSideEffectLedger(self.controller.db_path)
        self.assertEqual("executing", ledger.get(outer_key)["state"])
        self.assertEqual("completed", ledger.get(inner_key)["state"])

        restarted = ExecutionController(self.root)
        self._register(restarted)
        resumed = restarted.submit("receipt-gap-action-fixture", payload)
        self.assertIsNotNone(restarted.run_once(resumed.id))

        self.assertEqual("completed", restarted.task(resumed.id).state)
        self.assertEqual(["fixture-revision"], _CALLS)
        recovered = DurableSideEffectLedger(restarted.db_path)
        self.assertEqual("completed", recovered.get(outer_key)["state"])
        self.assertEqual("completed", recovered.get(inner_key)["state"])


if __name__ == "__main__":
    unittest.main()
