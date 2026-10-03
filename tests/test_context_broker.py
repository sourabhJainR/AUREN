from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / ".ai-harness" / "runtime" / "context_broker.py"


class ContextBrokerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module_name = "auren_context_broker_test"
        spec = importlib.util.spec_from_file_location(module_name, MODULE)
        cls.module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        # Dataclasses resolve postponed annotations through sys.modules. A
        # file-loaded module must be registered before exec_module().
        sys.modules[module_name] = cls.module
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("auren_context_broker_test", None)

    def test_selects_only_relevant_context_under_budget(self):
        loaded = []
        broker = self.module.ContextBroker(budget_chars=80, max_items=2)
        broker.register(self.module.ContextCandidate("auth", "source", "needed", lambda: loaded.append("auth") or "authentication service", relevance=.9, cost=25))
        broker.register(self.module.ContextCandidate("unrelated", "history", "not needed", lambda: loaded.append("unrelated") or "database migration", relevance=.1, cost=25))
        leases = broker.discover("authentication", phase="current")
        self.assertEqual([x.context_id for x in leases], ["auth"])
        self.assertEqual(loaded, ["auth"])

    def test_release_evicts_active_context_but_keeps_provenance(self):
        broker = self.module.ContextBroker(budget_chars=100)
        broker.register(self.module.ContextCandidate("policy", "policy", "required gate", lambda: "verification policy", relevance=.9, required=True))
        leases = broker.discover("verification", phase="current")
        self.assertEqual(len(leases), 1)
        digest = leases[0].digest
        broker.release()
        self.assertEqual(broker.active(), ())
        events = broker.telemetry()["events"]
        self.assertEqual(events[-1]["event"], "release")
        self.assertEqual(events[-1]["digest"], digest)

    def test_required_context_cannot_silently_overflow_budget(self):
        broker = self.module.ContextBroker(budget_chars=20)
        broker.register(self.module.ContextCandidate("contract", "task", "required", lambda: "x" * 30, required=True))
        with self.assertRaises(RuntimeError):
            broker.discover("task", phase="current")


if __name__ == "__main__":
    unittest.main()
