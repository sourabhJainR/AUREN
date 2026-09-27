"""Regression contracts for capabilities inherited from superseded PRs."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from portable.agent_capabilities import (
    AutomationScheduler,
    CapabilityFabric,
    DelegationPool,
    OutputQualityGate,
    PersistentMemory,
    ProviderAdapter,
    ProviderAdapterRegistry,
    Skill,
    SkillRegistry,
)
from portable.adaptive_trigger import AdaptiveTrigger
from portable.ai_coding_agency_bridge import CodingTask, run_coding_task
from portable.hermes_runtime import HermesRuntime
from portable.trigger_runtime import TriggerRuntime


class LegacyLineageConformanceTests(unittest.TestCase):
    def test_hermes_capability_fabric_contract(self):
        discovered = CapabilityFabric().discover()
        for name in (
            "memory", "session_search", "skills", "cronjob",
            "background_processes", "provider_fallback", "delegate_task",
        ):
            self.assertIn(name, discovered)

    def test_hermes_memory_skill_and_runtime_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.db", require_approval=False)
            record = memory.remember(
                "project", "lesson", "verified engineering lesson",
                confidence=0.9, verified=True,
            )
            self.assertIsNotNone(record)
            self.assertTrue(memory.search("project", "engineering lesson")[0].verified)
            registry = SkillRegistry()
            registry.register(Skill("demo", "demo skill", "run safely"))
            self.assertEqual(registry.load("demo", []).name, "demo")
        self.assertIsNotNone(HermesRuntime())

    def test_provider_fallback_contract(self):
        registry = ProviderAdapterRegistry([
            ProviderAdapter("primary", frozenset({"chat"}), priority=10),
            ProviderAdapter("fallback", frozenset({"chat"}), priority=1),
        ])
        self.assertEqual(registry.resolve(["chat"]).name, "primary")
        self.assertEqual(registry.resolve(["chat"], preferred=["fallback"]).name, "fallback")

    def test_agency_bridge_contract(self):
        self.assertTrue(callable(run_coding_task))
        task = CodingTask(task_id="lineage", goal="verify canonical agency boundary")
        self.assertEqual(task.task_id, "lineage")

    def test_background_delegation_and_scheduler_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            pool = DelegationPool(max_workers=1)
            receipt, future = pool.submit("task-1", lambda: "ok")
            self.assertEqual(receipt.status, "running")
            self.assertEqual(future.result(timeout=2), "ok")
            pool.close()

            scheduler = AutomationScheduler(Path(directory) / "scheduler.db")
            schedule = scheduler.add("safe task", 60)
            self.assertTrue(scheduler.due())
            claim = scheduler.claim(schedule.id)
            self.assertTrue(claim)
            scheduler.finish(schedule.id, claim, "success")
            self.assertEqual(scheduler.recent_runs(schedule.id)[0]["status"], "success")
            scheduler.close()

    def test_output_quality_gate_contract(self):
        result = OutputQualityGate().evaluate(
            acceptance_met=True, verification_passed=True, evidence_count=1,
            diff_clean=True, scope_clean=True, unresolved=0,
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.score, 100)

    def test_adaptive_trigger_durable_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = AutomationScheduler(Path(directory) / "runtime.sqlite")
            runtime = TriggerRuntime(scheduler)
            done = threading.Event()

            def runner(request, trigger_id):
                done.set()
                return "accepted"

            trigger = AdaptiveTrigger(runtime, runner)
            receipt = trigger.trigger_adaptive_runtime(
                "lineage task", directory, {"source": "conformance"}, fire_and_forget=False
            )
            self.assertEqual(receipt.status, "pending")
            self.assertTrue(receipt.trigger_id)
            self.assertTrue(runtime.due(limit=1, kind="adaptive_runtime"))
            trigger.dispatch_once()
            self.assertTrue(done.wait(1.0))
            self.assertEqual(trigger.get_status(receipt.trigger_id).status, "success")
            trigger.close()
            scheduler.close()

    def test_portable_release_lineage_contract(self):
        plugin = json.loads(Path(".claude-plugin/plugin.json").read_text())
        self.assertGreaterEqual(tuple(map(int, plugin["version"].split("."))), (22, 1, 0))


if __name__ == "__main__":
    unittest.main()
