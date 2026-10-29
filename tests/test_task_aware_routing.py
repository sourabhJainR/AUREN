"""Regression tests for task-aware memory/resource routing."""
import tempfile
import unittest
from pathlib import Path

from portable.agent_capabilities import PersistentMemory
from portable.memory_orchestrator import MemoryOrchestrator, MemoryBudgetError
from portable.task_aware_routing import (
    ResourceCandidate,
    classify_task,
    make_receipt,
    plan_memory,
    recall_for_task,
    select_resources,
)


class TaskAwareRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.memory = PersistentMemory(root / "memory.sqlite3", require_approval=True)
        self.orchestrator = MemoryOrchestrator(root, "routing-tests", memory=self.memory)

    def tearDown(self):
        self.memory.close()

    def test_explicit_task_type_overrides_heuristic_and_invalid_type_fails(self):
        self.assertEqual(("research", "explicit task type"), classify_task("debug this", "research"))
        with self.assertRaisesRegex(ValueError, "unsupported task type"):
            plan_memory("do work", task_type="unknown")

    def test_classifier_does_not_match_partial_words(self):
        self.assertEqual("conversation", classify_task("classify this memory request")[0])
        self.assertEqual("coding", classify_task("class implementation")[0])

    def test_task_kinds_are_purposeful_and_deduplicated(self):
        coding = plan_memory("implement feature", task_type="coding", include_sensory=True)
        self.assertIn("procedural", coding.kinds)
        self.assertIn("hierarchical", coding.kinds)
        self.assertIn("sensory", coding.kinds)
        self.assertEqual(len(coding.kinds), len(set(coding.kinds)))
        conversation = plan_memory("hello", task_type="conversation")
        self.assertNotIn("reflective", conversation.kinds)
        self.assertNotIn("kv_cache", conversation.kinds)

    def test_memory_recall_uses_canonical_store_and_preserves_required_ids(self):
        saved = self.orchestrator.remember(
            "procedural", "verified fix: retry only after operation status is known",
            source="reviewed-run", approved=True, verified=True, confidence=0.95,
        )
        plan, recall = recall_for_task(
            self.orchestrator, "debug provider recovery",
            required_memory_ids=(saved.memory_id,), budget_chars=500,
        )
        self.assertEqual("debugging", plan.task_type)
        self.assertIn(saved.memory_id, [item.memory_id for item in recall.memories])
        self.assertIn("operation status is known", recall.text)

    def test_required_memory_kind_is_not_hidden_by_task_defaults(self):
        saved = self.orchestrator.remember(
            "procedural", "required procedural recovery note",
            source="approved-run", approved=True, verified=True,
        )
        plan, recall = recall_for_task(
            self.orchestrator, "procedural recovery note", task_type="conversation",
            required_memory_ids=(saved.memory_id,), budget_chars=300,
        )
        self.assertIn("procedural", plan.kinds)
        self.assertIn(saved.memory_id, [item.memory_id for item in recall.memories])

    def test_required_memory_respects_branch_scope_even_when_id_is_known(self):
        saved = self.orchestrator.remember(
            "procedural", "release procedure from feature branch",
            source="approved-run", approved=True, verified=True, branch="feature/safe",
        )
        with self.assertRaises(MemoryBudgetError):
            recall_for_task(
                self.orchestrator, "unrelated query", task_type="conversation",
                required_memory_ids=(saved.memory_id,), branch="feature/other",
                budget_chars=500,
            )

    def test_required_memory_budget_failure_is_not_silently_downgraded(self):
        saved = self.orchestrator.remember(
            "semantic", "critical policy context must not be truncated",
            source="policy", approved=True, verified=True,
        )
        with self.assertRaises(MemoryBudgetError):
            recall_for_task(
                self.orchestrator, "review policy", required_memory_ids=(saved.memory_id,),
                budget_chars=4,
            )

    def test_unknown_options_fail_closed(self):
        with self.assertRaisesRegex(TypeError, "unsupported memory routing options"):
            recall_for_task(self.orchestrator, "coding task", invent_new_policy=True)

    def test_resource_selection_checks_policy_before_ranking(self):
        candidates = [
            ResourceCandidate(
                "best-but-not-permitted", "plugin", frozenset({"code"}),
                available=True, permission_granted=False, privacy_compatible=True,
                risk="low", measured_quality=0.99, estimated_cost=0.1,
            ),
            ResourceCandidate(
                "private-safe", "builtin", frozenset({"code"}),
                available=True, permission_granted=True, privacy_compatible=True,
                risk="low", measured_quality=0.8, estimated_cost=0.2,
                estimated_latency_ms=100, failure_rate=0.05,
            ),
            ResourceCandidate(
                "privacy-mismatch", "provider", frozenset({"code"}),
                available=True, permission_granted=True, privacy_compatible=False,
                risk="low", measured_quality=1.0, estimated_cost=0.01,
            ),
        ]
        decision = select_resources(
            candidates, required_capabilities={"code"}, max_risk="medium",
            max_cost=1.0, require_measured_quality=True,
        )
        self.assertEqual(("private-safe",), decision.selected)
        rejected = dict(decision.rejected)
        self.assertEqual("permission not granted", rejected["best-but-not-permitted"])
        self.assertEqual("privacy constraints not satisfied", rejected["privacy-mismatch"])
        self.assertEqual(64, len(decision.policy_digest))

    def test_unknown_cost_or_latency_is_not_assumed_within_a_hard_budget(self):
        candidate = ResourceCandidate(
            "unknown-cost", "tool", frozenset({"search"}),
            available=True, permission_granted=True, privacy_compatible=True,
            risk="low", estimated_latency_ms=20, measured_quality=0.5,
        )
        decision = select_resources(
            [candidate], required_capabilities={"search"}, max_cost=1.0,
        )
        self.assertEqual((), decision.selected)
        self.assertEqual("cost unknown or exceeds budget", dict(decision.rejected)["unknown-cost"])

    def test_selection_is_deterministic_and_caps_composition(self):
        rows = [
            ResourceCandidate(
                name, "builtin", frozenset({"search"}), True, True, True, "low",
                estimated_cost=0.2, estimated_latency_ms=100,
                measured_quality=quality, failure_rate=0.1,
            )
            for name, quality in (("b", 0.8), ("a", 0.8), ("c", 0.4))
        ]
        first = select_resources(rows, required_capabilities={"search"}, max_candidates=2)
        second = select_resources(list(reversed(rows)), required_capabilities={"search"}, max_candidates=2)
        self.assertEqual(first.selected, second.selected)
        self.assertEqual(("a", "b"), first.selected)
        self.assertEqual("lower-ranked eligible candidate", dict(first.rejected)["c"])

    def test_malformed_boolean_and_nonfinite_resource_metadata_fail_closed(self):
        bad_flags = ResourceCandidate(
            "bad-flag", "plugin", frozenset({"search"}), available=True,
            permission_granted="false", privacy_compatible=True, risk="low",
        )
        bad_quality = ResourceCandidate(
            "nan-quality", "plugin", frozenset({"search"}), available=True,
            permission_granted=True, privacy_compatible=True, risk="low",
            measured_quality=float("nan"),
        )
        decision = select_resources(
            [bad_flags, bad_quality], required_capabilities={"search"},
        )
        self.assertEqual((), decision.selected)
        rejected = dict(decision.rejected)
        self.assertEqual("invalid boolean policy metadata", rejected["bad-flag"])
        self.assertEqual("invalid quality measurement", rejected["nan-quality"])

    def test_nonfinite_budget_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "max_cost"):
            select_resources([], required_capabilities=(), max_cost=float("nan"))

    def test_receipt_contains_digests_and_ids_not_memory_text(self):
        saved = self.orchestrator.remember(
            "semantic", "secret project detail should not enter receipt",
            source="approved-note", approved=True, verified=True,
        )
        plan, recall = recall_for_task(
            self.orchestrator, "research project detail", task_type="research",
            required_memory_ids=(saved.memory_id,), budget_chars=200,
        )
        receipt = make_receipt("research project detail", plan, recall)
        serialized = repr(receipt)
        self.assertIn(saved.memory_id, serialized)
        self.assertNotIn("secret project detail", serialized)
        self.assertEqual(64, len(receipt.query_digest))


if __name__ == "__main__":
    unittest.main()
