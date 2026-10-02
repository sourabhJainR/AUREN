import unittest

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness" / "runtime"))

from context_planner import EvidenceCandidate, choose_retrieval_recovery, plan_context, select_evidence


class ContextPlannerTests(unittest.TestCase):
    def test_high_risk_review_requires_fresh_verification_and_security(self):
        plan = plan_context(phase="review", risk="high", uncertainty="medium")
        self.assertTrue(plan.require_fresh_verification)
        self.assertIn("security", plan.retrieval_modes)
        self.assertIn("structural", plan.retrieval_modes)

    def test_high_uncertainty_uses_semantic_and_history(self):
        plan = plan_context(phase="investigate", risk="medium", uncertainty="high")
        self.assertIn("semantic", plan.retrieval_modes)
        self.assertIn("history", plan.retrieval_modes)

    def test_policy_strategy_changes_retrieval_order_without_removing_safety(self):
        plan = plan_context(phase="debug", risk="high", uncertainty="medium", policy_strategy="targeted_context")
        self.assertEqual(plan.policy_strategy, "targeted_context")
        self.assertEqual(plan.retrieval_modes[:3], ("instructions", "task_contract", "structural"))
        self.assertIn("security", plan.retrieval_modes)

    def test_recovery_pivots_to_working_mode_without_repeating_failed_mode(self):
        decision = choose_retrieval_recovery(
            failed_modes=("semantic",),
            available_modes=("semantic", "structural", "lexical"),
            working_modes=("structural", "semantic"),
        )
        self.assertEqual(decision.action, "pivot")
        self.assertEqual(decision.mode, "structural")
        self.assertTrue(decision.changed_strategy)
        self.assertFalse(decision.terminal)

    def test_recovery_stops_when_every_mode_has_failed(self):
        decision = choose_retrieval_recovery(
            failed_modes=("semantic", "structural", "lexical"),
            available_modes=("semantic", "structural", "lexical"),
        )
        self.assertEqual(decision.action, "stop")
        self.assertTrue(decision.terminal)
        self.assertIsNone(decision.mode)

    def test_selection_is_ranked_deduplicated_and_bounded(self):
        candidates = [
            EvidenceCandidate("low", "source", "low", relevance=.2, confidence=.9, freshness=.9, cost=100),
            EvidenceCandidate("best", "source", "best", relevance=.99, confidence=.95, freshness=.95, cost=100),
            EvidenceCandidate("best", "source", "duplicate", relevance=.99, confidence=.95, freshness=.95, cost=100),
            EvidenceCandidate("too-expensive", "source", "large", relevance=1, confidence=1, freshness=1, cost=1000),
        ]
        selected = select_evidence(candidates, budget=250, max_items=10)
        self.assertEqual([item.evidence_id for item in selected], ["best", "low"])
        self.assertLessEqual(sum(item.cost for item in selected), 250)


if __name__ == "__main__":
    unittest.main()
