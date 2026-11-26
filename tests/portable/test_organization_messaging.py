"""Regression tests for organization messaging ledger."""
import tempfile
import threading
import unittest
from pathlib import Path

from portable.organization_messaging import (
    AgentIdentity, OrganizationMessaging, MessageDenied, MessageConflict,
)


class MessagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"messages.sqlite"
        self.members={
            "lead":AgentIdentity("lead","acme","lead",("delegate",),()),
            "dev":AgentIdentity("dev","acme","developer",("implement",),()),
            "qa":AgentIdentity("qa","acme","qa",("review",),()),
            "other":AgentIdentity("other","other-org","other",(),()),
        }
        self.received=[]
        self.ledger=OrganizationMessaging(self.path,"acme",
            {k:v for k,v in self.members.items() if k!="other"},
            lambda s,r,m: s.organization_id==r.organization_id and m["organization_id"]=="acme",
            lambda r,m:self.received.append((r.agent_id,m)))
    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_message_delivers_and_thread_replies_correlate(self):
        receipt=self.ledger.enqueue("lead","dev","TASK_REQUEST",{"task":"implement"},
            idempotency_key="one",thread_id="task-1",correlation_id="task-1")
        self.assertEqual(receipt.state,"queued")
        delivered=self.ledger.dispatch(receipt.message_id)
        self.assertEqual(delivered.state,"delivered")
        self.assertEqual(self.received[0][0],"dev")
        self.assertEqual(self.ledger.thread("task-1")[0]["state"],"delivered")

    def test_denies_cross_org_and_unknown_members(self):
        with self.assertRaises(MessageDenied):
            self.ledger.enqueue("lead","missing","QUERY",{},idempotency_key="x")
        with self.assertRaises(ValueError):
            self.ledger.enqueue("lead","dev","NOPE",{},idempotency_key="x")

    def test_idempotency_and_conflict(self):
        first=self.ledger.enqueue("lead","dev","QUERY",{"q":"x"},idempotency_key="same")
        again=self.ledger.enqueue("lead","dev","QUERY",{"q":"x"},idempotency_key="same")
        self.assertEqual(first.message_id,again.message_id)
        with self.assertRaises(MessageConflict):
            self.ledger.enqueue("lead","dev","QUERY",{"q":"different"},idempotency_key="same")

    def test_guard_denial(self):
        ledger=OrganizationMessaging(self.path,"acme",self.ledger.members,
            lambda *_:False,lambda *_:None)
        with self.assertRaises(MessageDenied):
            ledger.enqueue("lead","dev","QUERY",{},idempotency_key="deny")
        ledger.close()

    def test_expiry_and_cancel(self):
        r=self.ledger.enqueue("lead","dev","QUERY",{},idempotency_key="cancel")
        self.assertEqual(self.ledger.cancel(r.message_id,"lead").state,"cancelled")
        with self.assertRaises(MessageDenied):
            self.ledger.cancel(r.message_id,"qa")

    def test_durable_restart(self):
        r=self.ledger.enqueue("lead","dev","QUERY",{"x":1},idempotency_key="restart")
        mid=r.message_id
        self.ledger.close()
        self.ledger=OrganizationMessaging(self.path,"acme",self.ledger.members,
            lambda *_:True,lambda *_:None)
        self.assertEqual(self.ledger.pending()[0],mid)



    def test_ambiguous_delivery_after_receiver_commit_is_reconciled_without_redispatch(self):
        accepted = set()
        deliveries = []

        def deliver(_recipient, envelope):
            deliveries.append(envelope["message_id"])
            accepted.add(envelope["message_id"])
            raise ConnectionError("acknowledgement lost after receiver commit")

        def reconcile(_recipient, envelope):
            return {
                "message_id": envelope["message_id"],
                "idempotency_key": envelope["idempotency_key"],
                "outcome": "delivered" if envelope["message_id"] in accepted else "not_delivered",
                "authoritative": True,
            }

        self.ledger.close()
        self.ledger = OrganizationMessaging(
            self.path, "acme",
            {k: v for k, v in self.members.items() if k != "other"},
            lambda *_: True, deliver, reconcile_delivery=reconcile,
        )
        receipt = self.ledger.enqueue(
            "lead", "dev", "TASK_REQUEST", {"task": "ship"},
            idempotency_key="uncertain-commit",
        )
        first = self.ledger.dispatch(receipt.message_id)
        self.assertEqual(first.state, "uncertain")
        self.assertEqual(self.ledger.pending(), [])
        self.assertEqual(self.ledger.uncertain(), [receipt.message_id])

        self.ledger.close()
        self.ledger = OrganizationMessaging(
            self.path, "acme",
            {k: v for k, v in self.members.items() if k != "other"},
            lambda *_: True, deliver, reconcile_delivery=reconcile,
        )
        recovered = self.ledger.reconcile(receipt.message_id)
        self.assertEqual(recovered.state, "delivered")
        self.assertEqual(self.ledger.dispatch(receipt.message_id).state, "delivered")
        self.assertEqual(deliveries, [receipt.message_id])

    def test_authoritative_not_delivered_reconciliation_is_required_before_retry(self):
        attempts = []

        def deliver(_recipient, envelope):
            attempts.append(envelope["message_id"])
            if len(attempts) == 1:
                raise TimeoutError("delivery response lost")

        def reconcile(_recipient, envelope):
            return {
                "message_id": envelope["message_id"],
                "idempotency_key": envelope["idempotency_key"],
                "outcome": "not_delivered",
                "authoritative": True,
            }

        self.ledger.close()
        self.ledger = OrganizationMessaging(
            self.path, "acme",
            {k: v for k, v in self.members.items() if k != "other"},
            lambda *_: True, deliver, reconcile_delivery=reconcile, max_attempts=2,
        )
        receipt = self.ledger.enqueue(
            "lead", "dev", "TASK_REQUEST", {"task": "retry"},
            idempotency_key="authoritative-not-delivered",
        )
        self.assertEqual(self.ledger.dispatch(receipt.message_id).state, "uncertain")
        self.assertEqual(self.ledger.pending(), [])
        self.assertEqual(self.ledger.reconcile(receipt.message_id).state, "retryable")
        self.assertEqual(self.ledger.pending(), [receipt.message_id])
        self.assertEqual(self.ledger.dispatch(receipt.message_id).state, "delivered")
        self.assertEqual(attempts, [receipt.message_id, receipt.message_id])

    def test_unknown_or_non_authoritative_reconciliation_never_requeues(self):
        def deliver(_recipient, _envelope):
            raise TimeoutError("outcome unknown")

        def unknown(_recipient, envelope):
            return {
                "message_id": envelope["message_id"],
                "idempotency_key": envelope["idempotency_key"],
                "outcome": "unknown",
                "authoritative": False,
            }

        self.ledger.close()
        self.ledger = OrganizationMessaging(
            self.path, "acme",
            {k: v for k, v in self.members.items() if k != "other"},
            lambda *_: True, deliver, reconcile_delivery=unknown,
        )
        receipt = self.ledger.enqueue("lead", "dev", "QUERY", {}, idempotency_key="unknown")
        self.assertEqual(self.ledger.dispatch(receipt.message_id).state, "uncertain")
        self.assertEqual(self.ledger.reconcile(receipt.message_id).state, "uncertain")
        self.assertEqual(self.ledger.pending(), [])

    def test_concurrent_dispatchers_cannot_claim_the_same_message(self):
        started = threading.Event()
        release = threading.Event()
        delivered = []

        def deliver(_recipient, envelope):
            delivered.append(envelope["message_id"])
            started.set()
            if not release.wait(5):
                raise TimeoutError("test delivery release timed out")

        receipt = self.ledger.enqueue("lead", "dev", "QUERY", {}, idempotency_key="one-claim")
        errors = []

        def first_worker():
            worker = OrganizationMessaging(
                self.path, "acme",
                {k: v for k, v in self.members.items() if k != "other"},
                lambda *_: True, deliver,
            )
            try:
                worker.dispatch(receipt.message_id)
            except Exception as exc:
                errors.append(exc)
            finally:
                worker.close()

        thread = threading.Thread(target=first_worker)
        thread.start()
        self.assertTrue(started.wait(5))
        with self.assertRaises(MessageDenied):
            self.ledger.dispatch(receipt.message_id)
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.ledger.dispatch(receipt.message_id).state, "delivered")
        self.assertEqual(delivered, [receipt.message_id])


    def test_late_delivery_failure_cannot_overwrite_reconciled_success(self):
        accepted = set()
        started = threading.Event()
        release = threading.Event()

        def deliver(_recipient, envelope):
            accepted.add(envelope["message_id"])
            started.set()
            if not release.wait(5):
                raise TimeoutError("test delivery release timed out")
            raise ConnectionError("late acknowledgement failure")

        def reconcile(_recipient, envelope):
            return {
                "message_id": envelope["message_id"],
                "idempotency_key": envelope["idempotency_key"],
                "outcome": "delivered" if envelope["message_id"] in accepted else "unknown",
                "authoritative": True,
            }

        self.ledger.close()
        self.ledger = OrganizationMessaging(
            self.path, "acme",
            {k: v for k, v in self.members.items() if k != "other"},
            lambda *_: True, lambda *_: None, reconcile_delivery=reconcile,
        )
        receipt = self.ledger.enqueue("lead", "dev", "QUERY", {}, idempotency_key="late-ack")
        worker_errors = []

        def dispatch_worker():
            worker = OrganizationMessaging(
                self.path, "acme",
                {k: v for k, v in self.members.items() if k != "other"},
                lambda *_: True, deliver,
            )
            try:
                worker.dispatch(receipt.message_id)
            except Exception as exc:
                worker_errors.append(exc)
            finally:
                worker.close()

        thread = threading.Thread(target=dispatch_worker)
        thread.start()
        self.assertTrue(started.wait(5))
        self.assertEqual(self.ledger.reconcile(receipt.message_id).state, "delivered")
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertEqual(self.ledger.dispatch(receipt.message_id).state, "delivered")


    def test_concurrent_enqueues_cannot_exceed_ledger_capacity(self):
        barrier = threading.Barrier(2)
        receipts = []
        denied = []

        def enqueue_worker(key):
            worker = OrganizationMessaging(
                self.path, "acme",
                {k: v for k, v in self.members.items() if k != "other"},
                lambda *_: True, lambda *_: None, max_messages=1,
            )
            try:
                barrier.wait(5)
                receipts.append(worker.enqueue(
                    "lead", "dev", "QUERY", {"key": key}, idempotency_key=key,
                ))
            except MessageDenied as exc:
                denied.append(exc)
            finally:
                worker.close()

        threads = [
            threading.Thread(target=enqueue_worker, args=("first",)),
            threading.Thread(target=enqueue_worker, args=("second",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(len(denied), 1)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

if __name__=="__main__":
    unittest.main()
