from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import gc
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.action_governance import ActionGovernanceGateway, ActionPolicy, ActionRequest
from portable.durable_approval import (
    ApprovalConflict,
    DurableApprovalStore,
    InvalidApprovalTransition,
)
from portable.execution_controller import ExecutionController
from portable.side_effect_ledger import DurableSideEffectLedger


def durable_approval_test_handler(payload):
    return {"success": True, "evidence": [payload["work_key"]]}


APPROVAL_FLOW = {}


def durable_approval_flow_handler(payload):
    from portable.durable_approval import ApprovalPending

    context = payload.get("_auren_execution_context")
    if not isinstance(context, dict) or not context.get("task_id"):
        raise RuntimeError("trusted execution context is missing")
    task_id = context["task_id"]
    request = ActionRequest(
        run_id="run-approval-flow", action_id="publish-approval-flow",
        actor="agent:writer", capability="publish", action="publish",
        target="customer/reply", risk="external_effect",
        idempotency_key=f"publish:{task_id}",
        evidence_refs=("sha256:" + "d" * 64,), task_id=task_id,
    )
    resume = payload.get("auren_approval_resume")
    if not isinstance(resume, dict) or resume.get("status") != "approved":
        APPROVAL_FLOW["retrieval_count"] += 1
        checkpoint = {
            "operation_id": "publish:approval-flow:v1",
            "artifact_digest": APPROVAL_FLOW["artifact"],
            "evidence_digest": APPROVAL_FLOW["evidence"],
            "policy_digest": APPROVAL_FLOW["policy"],
            "request_fingerprint": request.fingerprint(),
            "artifact_ref": "sha256:" + APPROVAL_FLOW["artifact"],
        }
        record = APPROVAL_FLOW["store"].create_request(
            request=request, operation_id=checkpoint["operation_id"],
            artifact_digest=checkpoint["artifact_digest"],
            evidence_digest=checkpoint["evidence_digest"],
            policy_digest=checkpoint["policy_digest"], requester_id="requester-1",
            required_role="release-approver", scope="repo:publish",
            expires_at=APPROVAL_FLOW["now"] + timedelta(hours=2),
            checkpoint=checkpoint,
        )
        APPROVAL_FLOW["approval_id"] = record.approval_id
        raise ApprovalPending(record.approval_id, checkpoint)
    approval_id = resume["approval_id"]
    checkpoint = resume["checkpoint"]
    result = APPROVAL_FLOW["store"].execute_approved_action(
        approval_id, request, APPROVAL_FLOW["gateway"],
        lambda: APPROVAL_FLOW["publish_calls"].append(task_id) or {"published": True},
        artifact_digest=checkpoint["artifact_digest"],
        evidence_digest=checkpoint["evidence_digest"],
        policy_digest=checkpoint["policy_digest"],
    )
    return {
        "success": bool(getattr(result, "executed", False) or getattr(result, "replayed", False)),
        "evidence": [f"approval:{approval_id}"],
        "approval_receipts": [approval_id],
    }


class DurableApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)
        self.controller = ExecutionController(self.root)
        self.controller.register_handler(
            "tests.test_durable_approval.durable_approval_test_handler",
            durable_approval_test_handler,
            execution_class="read_only",
        )
        self.task = self.controller.submit(
            "tests.test_durable_approval.durable_approval_test_handler",
            {"work_key": "approval-test"},
            goal="durable approval regression",
        )
        self.artifact = "a" * 64
        self.evidence = "b" * 64
        self.policy = "c" * 64
        self.wakeups = []
        self.store = self.make_store()
        APPROVAL_FLOW.clear()
        APPROVAL_FLOW.update({
            "store": self.store, "artifact": self.artifact, "evidence": self.evidence,
            "policy": self.policy, "now": self.now, "retrieval_count": 0, "publish_calls": [],
        })

    def tearDown(self):
        # The shared approval flow is module-global; release its controller/ledger
        # references before Windows removes SQLite database and WAL files.
        APPROVAL_FLOW.clear()
        self.store = None
        self.controller = None
        gc.collect()
        self.temp.cleanup()

    def make_store(self):
        return DurableApprovalStore(
            self.controller,
            approver_authorizer=lambda approver, role, scope, roles: (
                approver in {"reviewer-1", "reviewer-2"}
                and role in roles and scope == "repo:publish"
            ),
            context_provider=lambda request: {
                "artifact_digest": self.artifact,
                "evidence_digest": self.evidence,
                "policy_digest": self.policy,
            },
            reconciliation_verifier=lambda record, outcome, ref: (
                ref.startswith("https://evidence.example/") and outcome in {"completed", "failed"}
            ),
            wake_callback=lambda task_id, approval_id: self.wakeups.append((task_id, approval_id)),
            clock=lambda: self.now,
        )

    def request(self, **overrides):
        values = dict(
            run_id="run-1", action_id="publish-1", actor="agent:writer",
            capability="publish", action="publish", target="customer/reply",
            risk="external_effect", idempotency_key="publish:task:1",
            evidence_refs=("sha256:" + "d" * 64,), task_id=self.task.id,
        )
        values.update(overrides)
        return ActionRequest(**values)

    def create(self, **overrides):
        values = dict(
            request=self.request(), operation_id="publish-reply-v1",
            artifact_digest=self.artifact, evidence_digest=self.evidence,
            policy_digest=self.policy, requester_id="requester-1",
            required_role="release-approver", scope="repo:publish",
            expires_at=self.now + timedelta(hours=2),
        )
        values.update(overrides)
        return self.store.create_request(**values)

    def approve(self, record, approver="reviewer-1"):
        return self.store.decide(
            record.approval_id, "approve", approver_id=approver,
            roles=("release-approver",), scope="repo:publish", reason="reviewed exact artifact",
        )

    def test_request_is_durable_and_idempotent_across_store_recreation(self):
        first = self.create()
        restarted = self.make_store()
        second = restarted.create_request(
            request=self.request(), operation_id="publish-reply-v1",
            artifact_digest=self.artifact, evidence_digest=self.evidence,
            policy_digest=self.policy, requester_id="requester-1",
            required_role="release-approver", scope="repo:publish",
            expires_at=self.now + timedelta(hours=2),
        )
        self.assertEqual(first.approval_id, second.approval_id)
        self.assertEqual(second.status, "pending")
        events = [
            row for row in self.controller.progress_journal._events()
            if row["task_id"] == self.task.id and row["event"] == "approval_state_transition"
        ]
        self.assertEqual(sum(first.approval_id in row["message"] for row in events), 1)

    def test_role_scope_and_separation_of_duties_fail_closed(self):
        record = self.create()
        with self.assertRaises(PermissionError):
            self.store.decide(
                record.approval_id, "approve", approver_id="reviewer-1",
                roles=("viewer",), scope="repo:publish",
            )
        with self.assertRaises(PermissionError):
            self.store.decide(
                record.approval_id, "approve", approver_id="requester-1",
                roles=("release-approver",), scope="repo:publish",
            )
        self.assertEqual(self.store.get(record.approval_id).status, "pending")

    def test_two_approvers_are_distinct_and_wake_only_after_threshold(self):
        record = self.create(required_approvals=2)
        first = self.approve(record, "reviewer-1")
        self.assertEqual(first.status, "pending")
        self.assertEqual(self.wakeups, [])
        duplicate = self.approve(record, "reviewer-1")
        self.assertEqual(duplicate.status, "pending")
        approved = self.approve(record, "reviewer-2")
        self.assertEqual(approved.status, "approved")
        self.assertEqual(self.wakeups, [(self.task.id, record.approval_id)])
        self.wakeups.clear()
        self.assertEqual(self.store.wake_approved(), 1)
        self.assertEqual(self.wakeups, [(self.task.id, record.approval_id)])

    def test_rejection_is_durable_and_duplicate_decision_is_idempotent(self):
        record = self.create()
        first = self.store.decide(
            record.approval_id, "reject", approver_id="reviewer-1",
            roles=("release-approver",), scope="repo:publish", reason="unsupported claim",
        )
        second = self.store.decide(
            record.approval_id, "reject", approver_id="reviewer-1",
            roles=("release-approver",), scope="repo:publish", reason="duplicate delivery",
        )
        self.assertEqual(first.status, "rejected")
        self.assertEqual(second.status, "rejected")
        with self.assertRaises(InvalidApprovalTransition):
            self.approve(record, "reviewer-2")

    def test_changed_artifact_supersedes_prior_pending_approval(self):
        first = self.create()
        self.artifact = "e" * 64
        second = self.create(artifact_digest=self.artifact)
        self.assertNotEqual(first.approval_id, second.approval_id)
        self.assertEqual(self.store.get(first.approval_id).status, "superseded")
        self.assertEqual(second.status, "pending")

    def test_changed_binding_after_approval_invalidates_before_execution(self):
        record = self.create()
        self.approve(record)
        changed = self.request(target="customer/other-reply")
        with self.assertRaises(ApprovalConflict):
            self.store.claim_execution(
                record.approval_id, changed, artifact_digest=self.artifact,
                evidence_digest=self.evidence, policy_digest=self.policy,
            )
        self.assertEqual(self.store.get(record.approval_id).status, "superseded")

    def test_expired_approval_cannot_be_decided_or_executed(self):
        record = self.create()
        self.now += timedelta(hours=3)
        with self.assertRaises(InvalidApprovalTransition):
            self.approve(record)
        self.assertEqual(self.store.get(record.approval_id).status, "expired")

    def test_gateway_execution_is_claimed_once_and_persisted(self):
        record = self.create()
        self.approve(record)
        ledger = DurableSideEffectLedger(self.root / "effects.sqlite3")
        gateway = ActionGovernanceGateway(
            ActionPolicy(
                allowed_capabilities=frozenset({"publish"}),
                approval_required_actions=frozenset({"publish"}),
                external_effect_actions=frozenset({"publish"}),
                require_durable_ledger_for_external_effects=True,
            ),
            approval_verifier=self.store.verify_receipt,
            side_effect_ledger=ledger,
        )
        calls = []
        result = self.store.execute_approved_action(
            record.approval_id, self.request(), gateway,
            lambda: calls.append("publish") or {"published": True},
            artifact_digest=self.artifact, evidence_digest=self.evidence,
            policy_digest=self.policy,
        )
        self.assertTrue(result.executed)
        self.assertEqual(calls, ["publish"])
        self.assertEqual(self.store.get(record.approval_id).status, "completed")
        with self.assertRaises(InvalidApprovalTransition):
            self.store.claim_execution(
                record.approval_id, self.request(), artifact_digest=self.artifact,
                evidence_digest=self.evidence, policy_digest=self.policy,
            )

    def test_exception_marks_unknown_and_requires_trusted_reconciliation(self):
        record = self.create()
        self.approve(record)
        gateway = ActionGovernanceGateway(
            ActionPolicy(
                allowed_capabilities=frozenset({"publish"}),
                approval_required_actions=frozenset({"publish"}),
                external_effect_actions=frozenset({"publish"}),
                require_durable_ledger_for_external_effects=True,
            ),
            approval_verifier=self.store.verify_receipt,
            side_effect_ledger=DurableSideEffectLedger(self.root / "ambiguous.sqlite3"),
        )
        calls = []

        def ambiguous():
            calls.append("remote accepted")
            raise TimeoutError("receipt lost")

        with self.assertRaises(TimeoutError):
            self.store.execute_approved_action(
                record.approval_id, self.request(), gateway, ambiguous,
                artifact_digest=self.artifact, evidence_digest=self.evidence,
                policy_digest=self.policy,
            )
        self.assertEqual(self.store.get(record.approval_id).status, "outcome_unknown")
        self.assertEqual(calls, ["remote accepted"])
        with self.assertRaises(InvalidApprovalTransition):
            self.store.claim_execution(
                record.approval_id, self.request(), artifact_digest=self.artifact,
                evidence_digest=self.evidence, policy_digest=self.policy,
            )
        reconciled = self.store.reconcile_outcome(
            record.approval_id, outcome="completed",
            evidence_ref="https://evidence.example/provider-operation/123",
        )
        self.assertEqual(reconciled.status, "completed")
        self.assertEqual(calls, ["remote accepted"])

    def test_interrupted_execution_becomes_unknown_not_retryable(self):
        record = self.create()
        self.approve(record)
        self.store.claim_execution(
            record.approval_id, self.request(), artifact_digest=self.artifact,
            evidence_digest=self.evidence, policy_digest=self.policy,
        )
        self.now += timedelta(minutes=10)
        self.assertEqual(self.store.recover_interrupted(older_than_seconds=60), 1)
        self.assertEqual(self.store.get(record.approval_id).status, "outcome_unknown")
        self.assertEqual(self.store.approved_for_resume(), ())

    def test_concurrent_claims_have_one_winner(self):
        record = self.create()
        self.approve(record)

        def claim():
            try:
                return self.store.claim_execution(
                    record.approval_id, self.request(), artifact_digest=self.artifact,
                    evidence_digest=self.evidence, policy_digest=self.policy,
                )
            except InvalidApprovalTransition:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _n: claim(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.store.get(record.approval_id).status, "executing")

    def test_restart_recovers_request_created_before_pause_checkpoint_was_written(self):
        self.controller.register_handler(
            "approval-flow", durable_approval_flow_handler,
            execution_class="approval_gated",
            approval_gate_policy={
                "approval_store": "DurableApprovalStore",
                "gateway": "ActionGovernanceGateway",
                "durable_side_effect_ledger": True,
            },
        )
        task = self.controller.submit(
            "approval-flow", {"auren_approval_aware": True, "work_key": "restart-before-pause"},
            goal="recover approval created before process interruption",
        )
        request = self.request(task_id=task.id)
        checkpoint = {
            "operation_id": "publish:restart-before-pause",
            "artifact_digest": self.artifact, "evidence_digest": self.evidence,
            "policy_digest": self.policy, "request_fingerprint": request.fingerprint(),
            "artifact_ref": "sha256:" + self.artifact,
        }
        record = self.store.create_request(
            request=request, operation_id=checkpoint["operation_id"],
            artifact_digest=self.artifact, evidence_digest=self.evidence, policy_digest=self.policy,
            requester_id="requester-1", required_role="release-approver", scope="repo:publish",
            expires_at=self.now + timedelta(hours=2), checkpoint=checkpoint,
        )
        stale = "2020-01-01T00:00:00+00:00"
        with sqlite3.connect(self.controller.db_path) as db:
            db.execute(
                "UPDATE tasks SET state='running',heartbeat_at=?,lease_until=?,lease_generation=1,"
                "lease_token='stale-worker' WHERE id=?",
                (stale, stale, task.id),
            )
        restarted_controller = ExecutionController(self.root)
        self.assertEqual(restarted_controller.task(task.id).state, "waiting_approval")
        restarted_store = DurableApprovalStore(
            restarted_controller, approver_authorizer=self.store.approver_authorizer,
            context_provider=self.store.context_provider,
            reconciliation_verifier=self.store.reconciliation_verifier, clock=lambda: self.now,
        )
        restarted_store.wake_callback = lambda task_id, approval_id: restarted_controller.resume_approved_task(
            task_id, approval_id, approval_store=restarted_store,
        )
        self.assertEqual(restarted_store.checkpoint_for(record.approval_id), checkpoint)
        self.assertEqual(restarted_store.get(record.approval_id).status, "pending")
        restarted_store.decide(
            record.approval_id, "approve", approver_id="reviewer-1",
            roles=("release-approver",), scope="repo:publish",
        )
        self.assertEqual(restarted_controller.task(task.id).state, "pending")
        self.assertEqual(
            restarted_controller.task(task.id).payload["auren_approval_resume"]["checkpoint"], checkpoint,
        )

    def test_rejected_approval_is_reconciled_without_process_restart(self):
        record = self.create()
        checkpoint = self.store.checkpoint_for(record.approval_id)
        with sqlite3.connect(self.controller.db_path) as db:
            payload = json.loads(db.execute(
                "SELECT payload FROM tasks WHERE id=?", (self.task.id,),
            ).fetchone()[0])
            payload["auren_approval_resume"] = {
                "approval_id": record.approval_id, "status": "pending",
                "checkpoint": checkpoint,
            }
            db.execute(
                "UPDATE tasks SET state='waiting_approval',payload=? WHERE id=?",
                (json.dumps(payload, sort_keys=True), self.task.id),
            )
        self.store.decide(
            record.approval_id, "reject", approver_id="reviewer-1",
            roles=("release-approver",), scope="repo:publish",
            reason="not approved",
        )
        self.controller.resume_pending()
        self.assertEqual(self.controller.task(self.task.id).state, "blocked_approval")
        self.assertEqual(self.store.get(record.approval_id).status, "rejected")

    def test_controller_pauses_and_resumes_same_checkpoint_without_retrieval_repeat(self):
        self.store.wake_callback = lambda task_id, approval_id: self.controller.resume_approved_task(
            task_id, approval_id, approval_store=self.store,
        )
        self.controller.register_handler(
            "approval-flow", durable_approval_flow_handler,
            execution_class="approval_gated",
            approval_gate_policy={
                "approval_store": "DurableApprovalStore",
                "gateway": "ActionGovernanceGateway",
                "durable_side_effect_ledger": True,
            },
        )
        APPROVAL_FLOW["gateway"] = ActionGovernanceGateway(
            ActionPolicy(
                allowed_capabilities=frozenset({"publish"}),
                approval_required_actions=frozenset({"publish"}),
                external_effect_actions=frozenset({"publish"}),
                require_durable_ledger_for_external_effects=True,
            ),
            approval_verifier=self.store.verify_receipt,
            side_effect_ledger=DurableSideEffectLedger(self.controller.db_path),
        )
        task = self.controller.submit(
            "approval-flow", {"auren_approval_aware": True, "work_key": "approval-flow"},
            goal="resume approval-bound publication",
        )
        self.assertIsNone(self.controller.run_once(task.id))
        self.assertEqual(self.controller.task(task.id).state, "waiting_approval")
        approval_id = APPROVAL_FLOW["approval_id"]
        self.assertEqual(self.store.get(approval_id).status, "pending")
        self.assertEqual(APPROVAL_FLOW["retrieval_count"], 1)
        self.store.decide(
            approval_id, "approve", approver_id="reviewer-1",
            roles=("release-approver",), scope="repo:publish",
        )
        self.assertEqual(self.controller.task(task.id).state, "pending")
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "completed")
        self.assertEqual(APPROVAL_FLOW["retrieval_count"], 1)
        self.assertEqual(APPROVAL_FLOW["publish_calls"], [task.id])
        self.assertEqual(self.store.get(approval_id).status, "completed")

    def test_audit_chain_and_progress_reconciliation(self):
        record = self.create()
        self.approve(record)
        self.assertTrue(self.store.verify_audit_chain())
        count = self.store.reconcile_progress()
        self.assertEqual(count, 0)
        with sqlite3.connect(self.controller.db_path) as db:
            db.execute(
                "UPDATE durable_approval_events SET event_json=? WHERE approval_id=? AND event_type='approval_requested'",
                ('{"tampered":true}', record.approval_id),
            )
        self.assertFalse(self.store.verify_audit_chain())


if __name__ == "__main__":
    unittest.main()
