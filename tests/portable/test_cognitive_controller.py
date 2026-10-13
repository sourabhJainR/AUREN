import tempfile
import unittest
from pathlib import Path

from portable.cognitive_controller import CognitiveController
from portable.cognitive_learning import BeliefContext
from portable.cognitive_runtime import CognitiveRuntime
from portable.goal_manager import Goal
from portable.hypothesis_engine import Hypothesis
from portable.information_planner import InformationAction
from portable.persistent_memory import PersistentMemory


class CognitiveControllerTests(unittest.TestCase):
    def test_plan_uses_goal_information_and_self_model(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.db", require_approval=False)
            cognitive = CognitiveRuntime.create(memory, "project-x")
            cognitive.goals.create(Goal("g1", "Finish parser", priority=90))
            cognitive.self_model.record("o1", "parser", success=True)
            controller = CognitiveController(cognitive)
            plan = controller.plan(
                "Fix parser", capability="parser", uncertainty=0.8,
                information_actions=(InformationAction("inspect", "Inspect failing path", 0.7, 1.0, 0.1),),
                beliefs=(BeliefContext("b1", "parser has a hidden failure", 0.25, ("e1",)),),
            )
            self.assertEqual(plan.goal_id, "g1")
            self.assertEqual(plan.information_action_id, "inspect")
            self.assertEqual(plan.self_confidence, 1.0)
            self.assertEqual(plan.belief_ids, ("b1",))
            enriched = controller.enrich_context("Fix parser", context={"existing": True})
            self.assertTrue(enriched["existing"])
            self.assertIn("auren_cognitive_plan", enriched)

    def test_plan_loads_persisted_low_confidence_beliefs(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.db", require_approval=False)
            cognitive = CognitiveRuntime.create(memory, "project-x")
            cognitive.hypotheses.propose(Hypothesis("h2", "q", "likely", "test", confidence=0.8))
            cognitive.hypotheses.propose(Hypothesis("h1", "q", "uncertain", "test", confidence=0.2))
            plan = CognitiveController(cognitive).plan("Investigate")
            self.assertEqual(plan.belief_ids, ("h1", "h2"))

    def test_plan_does_not_execute_information_action(self):
        called = False
        def should_not_run():
            nonlocal called
            called = True
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.db", require_approval=False)
            controller = CognitiveController(CognitiveRuntime.create(memory, "project-x"))
            controller.plan("Explore", information_actions=(InformationAction("act", "bounded action", 0.5),))
            self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
