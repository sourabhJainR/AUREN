"""End-to-end consequential recovery checks through ExecutionController.run_once."""
import tempfile
import unittest
from pathlib import Path

from portable.execution_controller import ExecutionController


_PROVIDER_CALLS = []


def recovery_context_provider(request):
    return {"trusted": True}


def recovery_ci_provider(request):
    return {"status": "completed", "conclusion": "success"}


def ambiguous_provider_action(payload):
    _PROVIDER_CALLS.append(payload["revision"])
    # Model a remote operation accepted by the provider whose response is lost.
    raise TimeoutError("provider accepted request but response was lost")


class ConsequentialRunOnceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        _PROVIDER_CALLS.clear()
        self.root = Path(self.temp.name)
        self.controller = ExecutionController(self.root)
        self._register(self.controller)

    @staticmethod
    def _register(controller):
        controller.register_handler(
            "recovery-context-provider",
            recovery_context_provider,
            execution_class="context_provider",
        )
        controller.register_handler(
            "recovery-ci-provider",
            recovery_ci_provider,
            execution_class="context_provider",
        )
        controller.register_handler(
            "consequential-recovery-action",
            ambiguous_provider_action,
            promotion_gate_policy={"context_handler": "recovery-context-provider"},
            ci_gate_policy={
                "expected_head_sha": "a" * 40,
                "required_checks": ["required-ci"],
                "evidence_handler": "recovery-ci-provider",
            },
            execution_class="consequential",
        )
        # This test isolates durable dispatch recovery; gate semantics have their
        # own regression suite and are not weakened in production.
        controller._preflight_gates = lambda task: (True, "test gate passed", [])

    @staticmethod
    def _payload():
        return {
            "target": "staging",
            "revision": "abc123",
            "auren_idempotency": {"key": "run-recovery-1:deploy:staging"},
        }

    def test_operator_resume_reconciles_status_without_redispatch(self):
        first = self.controller.submit("consequential-recovery-action", self._payload())
        self.assertIsNone(self.controller.run_once(first.id))
        self.assertEqual("blocked_reconciliation", self.controller.task(first.id).state)
        clarification = self.controller.company.open_clarifications(first.work_unit_id)[0]

        self_outer = self
        class Adapter:
            observed_operation_id = None

            def lookup_operation(self, operation_id, idempotency_key):
                from portable.side_effect_ledger import DurableSideEffectLedger
                self.observed_operation_id = operation_id
                digest = DurableSideEffectLedger(self_outer.controller.db_path).get(
                    idempotency_key
                )["payload_digest"]
                return {
                    "status": "completed",
                    "evidence_ref": "https://provider.example/operations/remote-99",
                    "receipt": {
                        "status": "completed",
                        "result": {"success": True, "detail": "remote outcome verified",
                                   "evidence": ["https://provider.example/operations/remote-99"]},
                        "provider_operation_id": "remote-99",
                        "operation_id": operation_id,
                        "idempotency_key": idempotency_key,
                        "provider": "consequential-recovery-action",
                        "intent_digest": digest,
                    },
                }

            def verify_reconciliation(self, key, evidence_ref, receipt):
                from portable.side_effect_ledger import DurableSideEffectLedger
                record = DurableSideEffectLedger(self_outer.controller.db_path).get(key)
                return (
                    key.startswith("provider-operation:")
                    and evidence_ref == "https://provider.example/operations/remote-99"
                    and receipt.get("provider_operation_id") == "remote-99"
                    and receipt.get("operation_id") == self.observed_operation_id
                    and receipt.get("idempotency_key") == key
                    and receipt.get("provider") == "consequential-recovery-action"
                    and receipt.get("intent_digest") == record["payload_digest"]
                    and receipt.get("result", {}).get("success") is True
                )

        self.controller.register_provider_operation_adapter("consequential-recovery-action", Adapter())
        self.assertTrue(self.controller.resume_blocked_provider_task(
            first.id, clarification_id=clarification["id"], operator_id="operator-7",
            operator_reason="Verified the provider operation record; reconcile only.",
        ))
        self.assertEqual("pending", self.controller.task(first.id).state)
        score = self.controller.run_once(first.id)
        self.assertIsNotNone(score)
        self.assertEqual("completed", self.controller.task(first.id).state)
        self.assertEqual(["abc123"], _PROVIDER_CALLS)
        from portable.side_effect_ledger import DurableSideEffectLedger
        record = DurableSideEffectLedger(self.controller.db_path).get(
            "consequential-idempotency:run-recovery-1:deploy:staging"
        )
        self.assertEqual("completed", record["state"])

    def test_unknown_provider_outcome_after_restart_never_redispatches(self):
        first = self.controller.submit(
            "consequential-recovery-action", self._payload()
        )
        self.assertIsNone(self.controller.run_once(first.id))
        self.assertEqual("blocked_reconciliation", self.controller.task(first.id).state)
        self.assertEqual("waiting_external", self.controller.company._state(first.work_unit_id)[0])
        self.assertEqual(["abc123"], _PROVIDER_CALLS)

        # Recreate the controller as a fresh worker over the same durable store.
        restarted = ExecutionController(self.root)
        self._register(restarted)
        resumed = restarted.submit(
            "consequential-recovery-action", self._payload()
        )
        self.assertIsNone(restarted.run_once(resumed.id))
        self.assertEqual("blocked_reconciliation", restarted.task(resumed.id).state)

        self.assertEqual(["abc123"], _PROVIDER_CALLS)
        from portable.side_effect_ledger import DurableSideEffectLedger

        record = DurableSideEffectLedger(restarted.db_path).get(
            "consequential-idempotency:run-recovery-1:deploy:staging"
        )
        self.assertEqual("reconcile_required", record["state"])


if __name__ == "__main__":
    unittest.main()
