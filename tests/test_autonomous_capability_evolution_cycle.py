import tempfile
import unittest
from pathlib import Path

from portable.autonomous_capability_invention import CapabilityComposition, HoldoutResult, SafetyResult
from portable.experience_router import ExperienceRouter
from portable.generalization_curriculum import ExperimentResult
from portable.persistent_memory import PersistentMemory
from portable.world_mega_model import WorldMegaModel


class AutonomousCapabilityEvolutionCycleTests(unittest.TestCase):
    def _model(self, root):
        memory = PersistentMemory(root / "memory.sqlite3", require_approval=False)
        return WorldMegaModel(memory, "evolution-demo", experience_router=ExperienceRouter(root))

    def test_full_cycle_blocks_promotion_without_decision_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = self._model(root)
            incumbent = CapabilityComposition(
                "default:local:standard:base",
                ("base",), "default", "local", "standard",
            )

            def invention_eval(candidate, holdout_id):
                return HoldoutResult(
                    holdout_id, 0.92, 0.80, True, True,
                    (f"{candidate.id}:{holdout_id}",),
                )

            def safety(candidate):
                return SafetyResult(True, (f"safety:{candidate.id}",))

            def generalization_eval(experiment):
                return ExperimentResult(experiment.id, 0.90, f"gen:{experiment.id}", True)

            def canary(candidate, index):
                return (0.90, f"canary:{index}", True)

            cycle = model.run_autonomous_capability_evolution_cycle(
                "planning-gap",
                incumbent=incumbent,
                available_capabilities=("base", "reasoning", "verification"),
                task_families=("search", "reasoning"),
                invention_holdout_ids=("h1", "h2", "h3"),
                invention_evaluator=invention_eval,
                safety_gate=safety,
                generalization_evaluator=generalization_eval,
                canary_evaluator=canary,
                capabilities_for_pathway=("verification",),
                baseline_score=0.80,
                curriculum_budget=2,
            )

            self.assertEqual(cycle.status, "promotion_blocked")
            self.assertIsNotNone(cycle.generalization)
            self.assertTrue(cycle.generalization.generalized)
            self.assertIsNotNone(cycle.lifecycle)
            self.assertEqual(cycle.lifecycle.state, "promotion_blocked")
            self.assertTrue(any("decision record is required" in reason for reason in cycle.reasons))
            self.assertIsNone(cycle.pathway, "pathway selection must not follow a blocked promotion")

    def test_failed_canary_rolls_back_before_pathway_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = self._model(root)
            incumbent = CapabilityComposition(
                "default:local:standard:base",
                ("base",), "default", "local", "standard",
            )

            def invention_eval(candidate, holdout_id):
                return HoldoutResult(0 if False else holdout_id, 0.92, 0.80, True, True, (f"e:{holdout_id}",))

            def safety(candidate):
                return SafetyResult(True, (f"s:{candidate.id}",))

            def generalization_eval(experiment):
                return ExperimentResult(experiment.id, 0.90, f"g:{experiment.id}", True)

            def canary(candidate, index):
                return (0.90 if index == 0 else 0.60, f"c:{index}", True)

            cycle = model.run_autonomous_capability_evolution_cycle(
                "rollback-gap",
                incumbent=incumbent,
                available_capabilities=("base", "reasoning"),
                task_families=("search", "reasoning"),
                invention_holdout_ids=("h1", "h2", "h3"),
                invention_evaluator=invention_eval,
                safety_gate=safety,
                generalization_evaluator=generalization_eval,
                canary_evaluator=canary,
                curriculum_budget=2,
                baseline_score=0.80,
            )

            self.assertEqual(cycle.status, "rolled_back")
            self.assertIsNotNone(cycle.lifecycle)
            self.assertEqual(cycle.lifecycle.state, "rolled_back")
            self.assertIsNone(cycle.pathway)


if __name__ == "__main__":
    unittest.main()
