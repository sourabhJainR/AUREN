import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from portable.automation_scheduler import AutomationScheduler
from portable.capability_fabric import CAPABILITIES, CapabilityFabric
from portable.output_quality import OutputQualityGate
from portable.persistent_memory import PersistentMemory


class CapabilityFabricTests(unittest.TestCase):
    def test_default_fabric_covers_runtime_capabilities(self):
        fabric = CapabilityFabric.default()
        found = fabric.discover()
        self.assertTrue(set(CAPABILITIES).issubset(found))
        self.assertTrue(found["terminal"].requires_sandbox)
        self.assertTrue(found["browser"].requires_network)

    def test_network_denied_capability_rejects_network_dependent_fallback(self):
        fabric = CapabilityFabric.default()
        # browser -> web_search is not a safe fallback when both require network.
        with self.assertRaisesRegex(RuntimeError, "fallback still requires network"):
            fabric.plan(["browser"], network_allowed=False)
        self.assertEqual(fabric.plan(["browser"], network_allowed=True)[0].name, "browser")


class PersistentMemoryTests(unittest.TestCase):
    def test_memory_is_intent_scoped_searchable_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemory(Path(tmp) / "memory.db", require_write_approval=True)
            self.assertIsNone(store.remember("lesson", "api_key=topsecret", intent_digest="a", approved=False))
            record = store.remember("lesson", "Prefer smaller bounded changes", intent_digest="a", approved=True, verified=True, confidence=0.9)
            self.assertIsNotNone(record)
            found = store.search("bounded changes", intent_digest="a")
            self.assertEqual([item.id for item in found], [record.id])
            self.assertEqual(store.search("bounded", intent_digest="other"), [])
            secret = store.remember("lesson", "token=abc123", intent_digest="a", approved=True)
            self.assertEqual(secret.text, "token=<redacted>")
            store.close()


class AutomationTests(unittest.TestCase):
    def test_due_claim_and_bounded_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            scheduler = AutomationScheduler(Path(tmp) / "automation.db")
            schedule = scheduler.add("run audit", 60, max_attempts=2, start=now)
            due = scheduler.due(now)
            self.assertEqual([item.id for item in due], [schedule.id])
            claim = scheduler.claim(schedule.id, now=now)
            self.assertIsNotNone(claim)
            scheduler.finish(claim, "retryable", "provider timeout", now=now)
            self.assertEqual(len(scheduler.recent_runs(schedule.id)), 1)
            self.assertEqual(scheduler.due(now), [])
            scheduler.close()


class OutputQualityTests(unittest.TestCase):
    def test_pristine_report_requires_proof(self):
        gate = OutputQualityGate()
        report = {"outcome": "done", "changed_files": ["x.py"], "verification": ["tests pass"], "risks": [], "incomplete_checks": []}
        blocked = gate.evaluate(report, acceptance_met=True, verification_passed=True, diff_clean=True, evidence_count=0, scope_clean=True)
        self.assertEqual(blocked.status, "blocked")
        ready = gate.evaluate(report, acceptance_met=True, verification_passed=True, diff_clean=True, evidence_count=2, scope_clean=True)
        self.assertEqual(ready.status, "ready")
        self.assertGreaterEqual(ready.score, 90)


if __name__ == "__main__":
    unittest.main()
