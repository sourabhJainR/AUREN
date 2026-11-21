from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

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

    def test_canonical_telemetry_path_precedes_legacy_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical.jsonl"
            legacy = root / "legacy.jsonl"
            with patch.dict(os.environ, {
                "AUREN_CONTEXT_BROKER_TELEMETRY": str(canonical),
                "AER_CONTEXT_BROKER_TELEMETRY": str(legacy),
            }, clear=True):
                broker = self.module.ContextBroker()
                broker._emit({"event": "test"})
            self.assertTrue(canonical.exists())
            self.assertFalse(legacy.exists())

    def test_legacy_telemetry_path_remains_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy.jsonl"
            with patch.dict(os.environ, {"AER_CONTEXT_BROKER_TELEMETRY": str(legacy)}, clear=True):
                broker = self.module.ContextBroker()
                broker._emit({"event": "test"})
            self.assertTrue(legacy.exists())


    def test_scoped_cache_is_used_only_with_fresh_resolved_scope(self):
        sys.path.insert(0, str(ROOT / ".ai-harness"))
        from runtime.context_cache import ContextCacheScope, ContextPageCache

        scope_a = ContextCacheScope("tenant-a", "private", "ctx1", "model1", "tool1", "policy1", "repo1", "rubric1")
        scope_b = ContextCacheScope("tenant-b", "private", "ctx1", "model1", "tool1", "policy1", "repo1", "rubric1")
        cache = ContextPageCache()
        calls = []
        broker = self.module.ContextBroker(
            cache=cache,
            cache_scope_resolver=lambda candidate: scope_a,
        )
        candidate = self.module.ContextCandidate(
            "repository", "source", "repository source",
            lambda: calls.append("loaded") or "tenant A repository context",
            relevance=1.0, required=True,
        )
        broker.register(candidate)
        first = broker.discover("repository", phase="current")
        broker.release()
        second = broker.discover("repository", phase="current")
        self.assertEqual(first[0].text, second[0].text)
        self.assertEqual(calls, ["loaded"])
        self.assertEqual(broker.telemetry()["events"][-2]["cache"], "hit")

        other = self.module.ContextBroker(
            cache=cache,
            cache_scope_resolver=lambda candidate: scope_b,
        )
        other.register(self.module.ContextCandidate(
            "repository", "source", "repository source",
            lambda: "tenant B repository context", relevance=1.0, required=True,
        ))
        self.assertEqual("tenant B repository context", other.discover("repository", phase="current")[0].text)

    def test_cache_and_scope_resolver_must_be_configured_together(self):
        from unittest.mock import Mock
        with self.assertRaises(ValueError):
            self.module.ContextBroker(cache=Mock())
        with self.assertRaises(ValueError):
            self.module.ContextBroker(cache_scope_resolver=lambda candidate: None)

    def test_required_context_cannot_silently_overflow_budget(self):
        broker = self.module.ContextBroker(budget_chars=20)
        broker.register(self.module.ContextCandidate("contract", "task", "required", lambda: "x" * 30, required=True))
        with self.assertRaises(RuntimeError):
            broker.discover("task", phase="current")


if __name__ == "__main__":
    unittest.main()
