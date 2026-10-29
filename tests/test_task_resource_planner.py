import unittest

from portable.agent_capabilities import CapabilityFabric
from portable.memory_orchestrator import MemoryBudgetError, MemoryEnvelope, MemoryRecall
from portable.task_resource_planner import plan_task_resources


class FakeMemory:
    def __init__(self, memories, *, omitted=0):
        self.memories = tuple(memories)
        self.omitted = omitted
        self.calls = []

    def recall(self, query, **kwargs):
        self.calls.append((query, kwargs))
        required = set(kwargs.get("required_memory_ids", ()))
        found = {item.memory_id for item in self.memories}
        if required - found:
            raise MemoryBudgetError("one or more required memories were not found or did not fit")
        return MemoryRecall(
            memories=self.memories,
            text="\n".join(item.text for item in self.memories),
            chars_used=sum(len(item.text) for item in self.memories),
            budget_chars=kwargs["budget_chars"],
            cache_hits=0,
            cache_misses=0,
            omitted=self.omitted,
        )


def memory(memory_id, text, *, kind="semantic", evidence=(), verified=True):
    return MemoryEnvelope(
        memory_id=memory_id,
        kind=kind,
        text=text,
        source="test-source",
        confidence=0.9,
        verified=verified,
        evidence_ids=tuple(evidence),
        intent_digest="intent-digest",
        created_at="2026-10-30T00:00:00+00:00",
        branch="main",
        source_revision="abc123",
    )


class TaskResourcePlannerTests(unittest.TestCase):
    def test_plans_scoped_memory_and_bounded_chunks_with_evidence(self):
        mem = FakeMemory([
            memory("policy-1", "Keep policy and rollback requirements.", kind="procedural", evidence=("ev-1",)),
            memory("large-1", "implementation detail " * 50, evidence=("ev-2",)),
        ], omitted=3)
        plan = plan_task_resources(
            mem, CapabilityFabric(), "implement bounded response handling",
            ["terminal", "memory"], task_class="implementation",
            required_memory_ids=["policy-1"], memory_budget_chars=5000,
            chunk_chars=256, sandbox_available=True,
        )
        self.assertIn("policy-1", plan.memory_ids)
        self.assertEqual(plan.omitted_memory_count, 3)
        self.assertGreater(len(plan.chunks), 1)
        self.assertIn("stream_chunks", plan.adaptive_strategies)
        self.assertIn("ev-1", {ev for chunk in plan.chunks for ev in chunk["evidence_ids"]})
        self.assertFalse(plan.sampling_permitted)
        self.assertEqual(len(plan.receipt_digest), 64)
        self.assertEqual(mem.calls[0][1]["kinds"][0], "working")

    def test_sampling_is_opt_in_and_exploratory_only(self):
        mem = FakeMemory([memory("m1", "small fact")], omitted=2)
        exploratory = plan_task_resources(
            mem, CapabilityFabric(), "explore possible implementation options", ["memory"],
            task_class="exploratory", allow_partial_exploration=True,
        )
        self.assertTrue(exploratory.sampling_permitted)
        protected = plan_task_resources(
            mem, CapabilityFabric(), "review security policy", ["memory"],
            task_class="security", allow_partial_exploration=True,
        )
        self.assertFalse(protected.sampling_permitted)

    def test_unknown_capability_fails_closed(self):
        with self.assertRaisesRegex(KeyError, "unknown capability"):
            plan_task_resources(
                FakeMemory([memory("m1", "fact")]), CapabilityFabric(),
                "implement", ["made_up_provider"], task_class="implementation",
            )

    def test_network_fallback_cannot_reintroduce_network_requirement(self):
        with self.assertRaisesRegex(RuntimeError, "fallback still requires network"):
            CapabilityFabric().plan(
                ["browser"], network_allowed=False, sandbox_available=True,
            )

    def test_empty_task_and_invalid_task_class_are_rejected(self):
        mem = FakeMemory([memory("m1", "fact")])
        with self.assertRaisesRegex(ValueError, "non-empty"):
            plan_task_resources(mem, CapabilityFabric(), " ", ["memory"])
        with self.assertRaisesRegex(ValueError, "task_class"):
            plan_task_resources(mem, CapabilityFabric(), "valid task", ["memory"], task_class="unknown")


if __name__ == "__main__":
    unittest.main()
