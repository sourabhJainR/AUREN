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


if __name__=="__main__":
    unittest.main()
