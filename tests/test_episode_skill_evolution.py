import tempfile
import unittest
from pathlib import Path

from portable.engineering_episode import EngineeringEpisode
from portable.episode_skill_evolution import EpisodeSkillEvolution, EpisodeSkillReplayCorpus, ReplayCase
from portable.persistent_memory import PersistentMemory
from portable.skill_optimization import SkillScore


class EpisodeSkillEvolutionTests(unittest.TestCase):
    def memory(self):
        self.tmp = tempfile.TemporaryDirectory()
        return PersistentMemory(Path(self.tmp.name) / "memory.db")

    @staticmethod
    def completed(episode_id, task_id, family="coding"):
        ep = EngineeringEpisode.start(
            episode_id=episode_id, task_id=task_id, project="p1",
            repository_snapshot="repo", intent_digest=f"intent-{task_id}",
            evidence_ids=(f"evidence-{task_id}",),
        )
        ep = ep.with_plan(f"plan-{task_id}", capability="coding").start_execution()
        ep = ep.begin_verification().record_verification(f"verify-{task_id}")
        ep = ep.begin_review().record_review(f"review-{task_id}")
        return ep.complete(
            outcome="passed",
            regression_ids=(f"reg-{task_id}",),
            learning_ids=(f"learn-{task_id}",),
        )

    def test_completed_episode_creates_candidate_replays_independently_and_stays_staged(self):
        memory = self.memory()
        try:
            bridge = EpisodeSkillEvolution(memory, "p1")
            source = self.completed("ep-source", "task-source")
            holdout = self.completed("ep-holdout", "task-holdout")
            bridge.corpus.register_episode(holdout, task_family="coding")

            def train_eval(case_id, skill):
                return SkillScore(0.9 if "verified coding" in skill else 0.5, 0.8)

            def independent_eval(case_id, skill):
                return SkillScore(0.95 if "verified coding" in skill else 0.4, 0.7)

            # Add a deterministic lesson to prove the episode itself is the proposal source.
            source = source._next(metadata=(("task_family", "coding"), ("skill_lesson", "use verified coding checks first")))
            result = bridge.evolve(
                episode=source,
                current_skill="Follow repository guidance.",
                evaluator=train_eval,
                independent_replay_evaluator=independent_eval,
            )
            self.assertTrue(result.optimization.accepted)
            self.assertTrue(result.replay.passed)
            self.assertTrue(result.staged)
            self.assertFalse(result.planning_eligible)
            self.assertIsNone(bridge.planning_skill())
        finally:
            memory.close()
            self.tmp.cleanup()

    def test_failure_dont_rule_is_persisted_and_can_seed_candidate(self):
        memory = self.memory()
        try:
            bridge = EpisodeSkillEvolution(memory, "p1")
            failed = EngineeringEpisode.start(
                episode_id="ep-failed", task_id="task-failed", project="p1",
                repository_snapshot="repo", intent_digest="intent-failed",
                evidence_ids=("evidence-failed",),
            ).with_plan("plan-failed", capability="coding").start_execution()
            failed = failed.fail(
                failure_class="flaky-test",
                dont_rules=("Do not use timing sleeps for synchronization.",),
            )
            holdout = self.completed("ep-holdout", "task-holdout")
            bridge.corpus.register_episode(holdout, task_family="coding")

            def evaluator(case_id, skill):
                return SkillScore(0.9 if "Do not use timing sleeps" in skill else 0.4, 0.8)

            result = bridge.evolve(
                episode=failed,
                current_skill="Run tests deterministically.",
                evaluator=evaluator,
                independent_replay_evaluator=evaluator,
                task_family="coding",
            )
            self.assertTrue(result.optimization.accepted)
            records = memory.search("p1", "timing sleeps")
            self.assertTrue(any(r.category == "failure-dont" and r.verified for r in records))
        finally:
            memory.close()
            self.tmp.cleanup()

    def test_promotion_requires_staged_result_and_evidence(self):
        memory = self.memory()
        try:
            bridge = EpisodeSkillEvolution(memory, "p1")
            source = self.completed("ep-source", "task-source")
            holdout = self.completed("ep-holdout", "task-holdout")
            bridge.corpus.register_episode(holdout, task_family="coding")
            evaluator = lambda case_id, skill: SkillScore(1.0 if "verified coding" in skill else 0.2, 0.5)
            source = source._next(metadata=(("task_family", "coding"), ("skill_rule", "use verified coding checks first")))
            result = bridge.evolve(
                episode=source,
                current_skill="Follow repository guidance.",
                evaluator=evaluator,
                independent_replay_evaluator=evaluator,
            )
            with self.assertRaises(ValueError):
                bridge.promote(result, promotion_evidence=())
            active = bridge.promote(result, promotion_evidence=("independent-replay-receipt",))
            self.assertIn("verified coding", active)
            self.assertEqual(bridge.planning_skill(), active)
        finally:
            memory.close()
            self.tmp.cleanup()

    def test_no_independent_holdout_fails_closed(self):
        memory = self.memory()
        try:
            bridge = EpisodeSkillEvolution(memory, "p1")
            source = self.completed("ep-source", "task-source")
            with self.assertRaises(ValueError):
                bridge.evolve(
                    episode=source,
                    current_skill="Follow repository guidance.",
                    evaluator=lambda *_: SkillScore(1.0, 1.0),
                    independent_replay_evaluator=lambda *_: SkillScore(1.0, 1.0),
                )
        finally:
            memory.close()
            self.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
