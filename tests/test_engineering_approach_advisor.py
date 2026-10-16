"""Regression tests for evidence-backed repository-native approach recommendations."""
import tempfile
import unittest
from pathlib import Path

from portable.engineering_design_guard import EngineeringDesignGuard


ROOT = Path(__file__).resolve().parents[1]


class EngineeringApproachAdvisorTests(unittest.TestCase):
    def test_memory_recommendations_reuse_current_owners_and_detect_stack(self):
        result = EngineeringDesignGuard.recommend_approaches(
            ROOT, "Improve memory context token use and compaction",
            ("portable/memory_orchestrator.py",),
        )
        self.assertFalse(result["needs_stack_confirmation"])
        self.assertIn("Python", result["stack"]["languages"])
        self.assertTrue(any("bounded retrieval" in item["pattern"] for item in result["recommendations"]))
        recommendation = result["recommendations"][0]
        self.assertIn("portable/context_engine.py", recommendation["repository_evidence"])
        self.assertTrue(any("unittest discover" in item for item in result["stack"]["versions"]))
        self.assertTrue(any("CI tests Python 3.11, 3.12" in item for item in result["stack"]["versions"]))

    def test_retry_recommendation_prefers_existing_durable_supervisor(self):
        result = EngineeringDesignGuard.recommend_approaches(
            ROOT, "Handle provider quota exhaustion and resume after reset", risk="high",
        )
        self.assertEqual("high", result["risk"])
        self.assertIn("rollback rehearsal", result["required_gates"])
        self.assertTrue(any("durable state machine" in item["pattern"] for item in result["recommendations"]))
        item = next(item for item in result["recommendations"] if "durable state machine" in item["pattern"])
        self.assertIn("portable/execution_controller.py", item["repository_evidence"])
        self.assertIn("without provider polling", item["approach"])

    def test_algorithm_recommendation_explains_complexity_and_tradeoffs(self):
        result = EngineeringDesignGuard.recommend_approaches(
            ROOT, "Deduplicate events by id while preserving input order",
        )
        item = next(item for item in result["recommendations"] if "hash-based deduplication" in item["pattern"])
        self.assertIn("O(n)", item["rationale"])
        self.assertIn("O(n) auxiliary memory", item["tradeoffs"])
        self.assertIn("portable/agent_capabilities.py", item["repository_evidence"])

    def test_unknown_repository_stack_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            result = EngineeringDesignGuard.recommend_approaches(
                Path(directory), "Implement a new capability",
            )
        self.assertTrue(result["needs_stack_confirmation"])
        self.assertEqual((), result["stack"]["languages"])
        self.assertTrue(result["recommendations"][0]["requires_stack_confirmation"])

    def test_empty_intent_is_rejected(self):
        with self.assertRaises(ValueError):
            EngineeringDesignGuard.recommend_approaches(ROOT, "  ")


if __name__ == "__main__":
    unittest.main()
