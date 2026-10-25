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
        self.controller = ExecutionController(
            self.root,
            provider_resume_authorizer=lambda _task, _clarification, operator, reason: (
                operator in {"operator-7", "operator-8"} and bool(reason.strip())
            ),
        )
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

    def test_operator_resume_requires_trusted_authorizer(self):
        controller = ExecutionController(self.root / "without-authorizer")
        self._register(controller)
        task = controller.submit("consequential-recovery-action", self._payload())
        self.assertIsNone(controller.run_once(task.id))
        self.assertEqual("blocked_reconciliation", controller.task(task.id).state)
        clarification = controller.company.open_clarifications(task.work_unit_id)[0]

        class Adapter:
            def lookup_operation(self, *_args):
                raise AssertionError("authorization must be checked before provider lookup")

            def verify_reconciliation(self, *_args):
                return False

        controller.register_provider_operation_adapter(task.handler, Adapter())
        with self.assertRaisesRegex(PermissionError, "trusted provider reconciliation resume authorizer"):
            controller.resume_blocked_provider_task(
                task.id,
                clarification_id=clarification["id"],
                operator_id="operator-claimed-by-caller",
                operator_reason="Attempted resume without authenticated host authorization.",
            )
        self.assertEqual("blocked_reconciliation", controller.task(task.id).state)
        self.assertEqual("open", controller.company.open_clarifications(task.work_unit_id)[0]["state"])
        self.assertEqual(["abc123"], _PROVIDER_CALLS)

    def test_operator_resume_rejects_truthy_non_boolean_authorization(self):
        controller = ExecutionController(
            self.root / "truthy-authorizer",
            provider_resume_authorizer=lambda *_args: "yes",
        )
        self._register(controller)
        task = controller.submit("consequential-recovery-action", self._payload())
        self.assertIsNone(controller.run_once(task.id))
        clarification = controller.company.open_clarifications(task.work_unit_id)[0]

        class Adapter:
            def lookup_operation(self, *_args):
                raise AssertionError("rejected authorization must not trigger provider lookup")

            def verify_reconciliation(self, *_args):
                return False

        controller.register_provider_operation_adapter(task.handler, Adapter())
        with self.assertRaisesRegex(PermissionError, "literal True"):
            controller.resume_blocked_provider_task(
                task.id,
                clarification_id=clarification["id"],
                operator_id="operator-7",
                operator_reason="A truthy string must not authorize the resume.",
            )
        self.assertEqual("blocked_reconciliation", controller.task(task.id).state)
        self.assertEqual("open", controller.company.open_clarifications(task.work_unit_id)[0]["state"])
        self.assertEqual(["abc123"], _PROVIDER_CALLS)

    def test_operator_resume_reconciles_status_without_redispatch(self):
        first = self.controller.submit("consequential-recovery-action", self._payload())
        self.assertIsNone(self.controller.run_once(first.id))
        self.assertEqual("blocked_reconciliation", self.controller.task(first.id).state)
        clarification = self.controller.company.open_clarifications(first.work_unit_id)[0]

        self_outer = self
        class Adapter:
            observed_operation_id = None

            def lookup_operation(self, operation_id, idempotency_key):
                from portable.organization_guard import evaluate_guard_policy
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


    def test_outer_reconciliation_rejects_truthy_non_boolean_provider_verifier(self):
        from portable.side_effect_ledger import DurableSideEffectLedger

        first = self.controller.submit("consequential-recovery-action", self._payload())
        self.assertIsNone(self.controller.run_once(first.id))
        self.assertEqual("blocked_reconciliation", self.controller.task(first.id).state)

        task = self.controller.task(first.id)
        outer_key = "consequential-idempotency:run-recovery-1:deploy:staging"
        provider_key = "provider-operation:" + outer_key
        evidence_ref = "https://provider.example/operations/remote-100"
        verification_calls = []

        class Adapter:
            def lookup_operation(self, operation_id, idempotency_key):
                record = DurableSideEffectLedger(self_outer.controller.db_path).get(idempotency_key)
                return {
                    "status": "completed",
                    "evidence_ref": evidence_ref,
                    "receipt": {
                        "status": "completed",
                        "result": {
                            "success": True,
                            "detail": "provider completion verified",
                            "evidence": [evidence_ref],
                        },
                        "provider_operation_id": "remote-100",
                        "operation_id": operation_id,
                        "idempotency_key": idempotency_key,
                        "provider": "consequential-recovery-action",
                        "intent_digest": record["payload_digest"],
                    },
                }

            def verify_reconciliation(self, key, ref, receipt):
                verification_calls.append((key, ref))
                if len(verification_calls) == 1:
                    return (
                        key == provider_key
                        and ref == evidence_ref
                        and receipt.get("provider_operation_id") == "remote-100"
                    )
                # The inner provider receipt has passed verification. The same
                # callback is invoked again for the supervisor's outer receipt;
                # a truthy non-boolean result must not authorize that transition.
                return "yes"

        self_outer = self
        self.controller.register_provider_operation_adapter(
            "consequential-recovery-action", Adapter(),
        )
        with self.assertRaisesRegex(PermissionError, "provider reconciliation evidence was rejected"):
            self.controller._invoke_consequential_with_ledger(
                task, self.controller._load_handler(task.handler),
            )

        ledger = DurableSideEffectLedger(self.controller.db_path)
        self.assertEqual(2, len(verification_calls))
        self.assertEqual("completed", ledger.get(provider_key)["state"])
        self.assertEqual("reconcile_required", ledger.get(outer_key)["state"])
        self.assertEqual("blocked_reconciliation", self.controller.task(first.id).state)
        self.assertEqual(["abc123"], _PROVIDER_CALLS)

    def test_restart_uses_durable_provider_receipt_if_outer_commit_was_interrupted(self):
        import hashlib
        import json
        import sqlite3
        from portable.provider_operation_lifecycle import (
            ProviderOperationLifecycle, ProviderOutcomeBlocked, stable_provider_operation_id,
        )
        from portable.side_effect_ledger import DurableSideEffectLedger

        first = self.controller.submit("consequential-recovery-action", self._payload())
        self.assertIsNone(self.controller.run_once(first.id))
        self.assertEqual("blocked_reconciliation", self.controller.task(first.id).state)

        task = self.controller.task(first.id)
        safe_payload = dict(task.payload)
        safe_payload.pop("auren_dispatch", None)
        idempotency = safe_payload.pop("auren_idempotency", None)
        with sqlite3.connect(self.controller.db_path) as db:
            rows = db.execute(
                "SELECT idempotency_key,state FROM durable_side_effects"
            ).fetchall()
        outer_rows = [str(key) for key, _state in rows if not str(key).startswith("provider-operation:")]
        self.assertEqual(1, len(outer_rows))
        outer_key = outer_rows[0]
        if outer_key.startswith("consequential-idempotency:"):
            stable_key = outer_key.removeprefix("consequential-idempotency:")
            identity = {"kind": "consequential_idempotency", "key": stable_key}
        elif outer_key.startswith("engineering-plan:"):
            stable_key = outer_key.removeprefix("engineering-plan:")
            identity = {"kind": "engineering_dispatch", "key": stable_key}
        else:
            stable_key = task.id
            identity = {"kind": "execution_task", "task_id": task.id}
        payload_digest = hashlib.sha256(
            json.dumps(safe_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        guard_decision = evaluate_guard_policy(None, {})
        binding = {
            "identity": identity,
            "handler": task.handler,
            "payload_digest": payload_digest,
            "guard": {
                "action": self.controller._handler_guard_action(task.handler),
                "policy_digest": guard_decision.policy_digest,
                "effect": guard_decision.effect,
                "matched_rule_ids": list(guard_decision.matched_rule_ids),
            },
        }
        provider_name = task.handler
        provider_intent = {"binding": binding, "payload": safe_payload}
        provider_key = "provider-operation:" + outer_key
        operation_id = stable_provider_operation_id(provider_key, provider_name, provider_intent)
        evidence_ref = "https://provider.example/operations/remote-99"
        ledger = DurableSideEffectLedger(self.controller.db_path, reconciliation_verifier=lambda *_: True)
        ledger.require_reconciliation(outer_key, "fault-injected outer receipt interruption")
        lifecycle = ProviderOperationLifecycle(ledger)
        with self.assertRaises(ProviderOutcomeBlocked):
            lifecycle.execute(
                provider_key, provider_name, provider_intent,
                lambda *_: (_ for _ in ()).throw(TimeoutError("provider accepted; worker died before outer commit")),
            )
        provider_digest = ledger.get(provider_key)["payload_digest"]
        receipt = {
            "status": "completed",
            "provider_operation_id": "remote-99",
            "operation_id": operation_id,
            "idempotency_key": provider_key,
            "provider": provider_name,
            "intent_digest": provider_digest,
            "result": {
                "success": True,
                "detail": "remote outcome verified",
                "evidence": [evidence_ref],
            },
        }
        lifecycle.reconcile(
            provider_key, provider_name, provider_intent,
            lambda *_: {
                "status": "completed", "evidence_ref": evidence_ref, "receipt": receipt,
            },
        )
        self.assertEqual("completed", ledger.get(provider_key)["state"])
        self.assertEqual("reconcile_required", ledger.get(outer_key)["state"])

        class Adapter:
            def lookup_operation(self, *_args):
                raise AssertionError("durable completed provider receipt must bypass another lookup")

            def verify_reconciliation(self, key, ref, candidate):
                return (
                    key == provider_key
                    and ref == evidence_ref
                    and candidate.get("provider_operation_id") == "remote-99"
                    and candidate.get("operation_id") == operation_id
                    and candidate.get("idempotency_key") == provider_key
                    and candidate.get("provider") == provider_name
                    and candidate.get("intent_digest") == provider_digest
                    and candidate.get("result", {}).get("success") is True
                )

        self.controller.register_provider_operation_adapter(task.handler, Adapter())
        clarification = self.controller.company.open_clarifications(first.work_unit_id)[0]
        self.assertTrue(self.controller.resume_blocked_provider_task(
            first.id, clarification_id=clarification["id"], operator_id="operator-8",
            operator_reason="Resume from the durable provider receipt after worker interruption.",
        ))
        score = self.controller.run_once(first.id)
        progress = [
            row for row in self.controller.progress_journal._events()
            if row.get("task_id") == first.id
        ]
        diagnostic = json.dumps({
            "task_state": self.controller.task(first.id).state,
            "progress": progress[-8:],
            "outer_record": ledger.get(outer_key),
            "provider_record": ledger.get(provider_key),
        }, sort_keys=True, default=str)
        self.assertIsNotNone(score, diagnostic)
        self.assertEqual("completed", self.controller.task(first.id).state)
        self.assertEqual(["abc123"], _PROVIDER_CALLS)
        self.assertEqual("completed", ledger.get(outer_key)["state"])

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
