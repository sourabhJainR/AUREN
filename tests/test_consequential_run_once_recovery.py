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
            "auren_dispatch": {"dispatch_key": "run-recovery-1:deploy:staging"},
        }

    def test_unknown_provider_outcome_after_restart_never_redispatches(self):
        first = self.controller.submit(
            "consequential-recovery-action", self._payload()
        )
        with self.assertRaisesRegex(TimeoutError, "response was lost"):
            self.controller.run_once(first.id)
        self.assertEqual("failed", self.controller.task(first.id).state)
        self.assertEqual(["abc123"], _PROVIDER_CALLS)

        # Recreate the controller as a fresh worker over the same durable store.
        restarted = ExecutionController(self.root)
        self._register(restarted)
        resumed = restarted.submit(
            "consequential-recovery-action", self._payload()
        )
        with self.assertRaisesRegex(RuntimeError, "requires reconciliation"):
            restarted.run_once(resumed.id)

        self.assertEqual(["abc123"], _PROVIDER_CALLS)
        from portable.side_effect_ledger import DurableSideEffectLedger

        record = DurableSideEffectLedger(restarted.db_path).get(
            "engineering-plan:run-recovery-1:deploy:staging"
        )
        self.assertEqual("reconcile_required", record["state"])


if __name__ == "__main__":
    unittest.main()
