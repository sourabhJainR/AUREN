import tempfile
import unittest
from pathlib import Path

from portable.adaptive_learning import AdaptiveLearningStore
from portable.engineering_episode import EngineeringEpisode
from portable.persistent_memory import PersistentMemory
from portable.skill_optimization import SkillScore


class AutomaticEpisodeMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = PersistentMemory(Path(self.tmp.name) / "memory.db")
        self.store = AdaptiveLearningStore(self.memory, "p1")

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def episode(self, episode_id, task_id):
        ep = EngineeringEpisode.start(
            episode_id=episode_id, task_id=task_id, project="p1",
            repository_snapshot="repo", intent_digest="intent",
            evidence_ids=("evidence-" + task_id,),
        ).with_plan("plan", capability="coding").start_execution()
        ep = ep.begin_verification().record_verification("verify")
        ep = ep.begin_review().record_review("review")
        return ep.complete(outcome="passed", regression_ids=("reg-" + task_id,))

    def test_maintenance_automatically_evolves_and_is_idempotent(self):
        source = self.episode("ep-source", "task-source")
        holdout = self.episode("ep-holdout", "task-holdout")
        from portable.episode_skill_evolution import EpisodeSkillEvolution
        bridge = EpisodeSkillEvolution(self.memory, "p1")
        bridge.corpus.register_episode(holdout, task_family="coding")
        source = source._next(metadata=(("task_family", "coding"), ("skill_lesson", "use verified coding checks first")))

        def evaluator(case_id, skill):
            return SkillScore(0.95 if "verified coding" in skill else 0.4, 0.8)

        results = self.store.process_engineering_episodes(
            episodes=(source,),
            current_skill="Follow repository guidance.",
            evaluator=evaluator,
            independent_replay_evaluator=evaluator,
        )
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].staged)
        self.assertIsNone(bridge.planning_skill())
        self.assertEqual(len(self.store.process_engineering_episodes(
            episodes=(source,),
            current_skill="Follow repository guidance.",
            evaluator=evaluator,
            independent_replay_evaluator=evaluator,
        )), 0)

    def test_regression_case_is_registered_in_shared_manifest(self):
        from portable.episode_skill_evolution import EpisodeSkillReplayCorpus
        corpus = EpisodeSkillReplayCorpus(self.memory, "p1")
        corpus.register_regression_case(case_id="r1", task_family="coding", task_id="task-r1")
        self.assertEqual(corpus.regression_case_ids(task_family="coding"), ("regression:r1",))


if __name__ == "__main__":
    unittest.main()
