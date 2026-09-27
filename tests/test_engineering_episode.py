import unittest

from portable.engineering_episode import EngineeringEpisode, EpisodeFinding, EpisodeGateError


class EngineeringEpisodeTests(unittest.TestCase):
    def episode(self):
        return EngineeringEpisode.start(
            episode_id="ep-1", task_id="task-1", project="p1",
            repository_snapshot="repo-1", intent_digest="intent-1",
            evidence_ids=("repo-fact",),
        )

    def test_closed_loop_requires_ordered_gates(self):
        ep = self.episode()
        with self.assertRaises(EpisodeGateError):
            ep.start_execution()
        ep = ep.with_plan("plan-1", capability="coding", resource_lane="local")
        ep = ep.start_execution().add_evidence("change-1")
        ep = ep.begin_verification().record_verification("verify-1")
        ep = ep.begin_review().record_review("review-1")
        ep = ep.complete(outcome="passed", regression_ids=("regression-1",))
        self.assertEqual(ep.phase.value, "completed")
        self.assertEqual(ep.repair_attempts, 0)

    def test_review_findings_force_repair_before_completion(self):
        ep = self.episode().with_plan("plan-1").start_execution().begin_verification().record_verification("verify-1").begin_review()
        finding = EpisodeFinding("f1", "security", "high", "unsafe boundary", ("verify-1",), "isolate execution")
        ep = ep.record_review("review-1", findings=(finding,))
        ep = ep.repair((finding,))
        with self.assertRaises(EpisodeGateError):
            ep.complete(outcome="passed", regression_ids=("reg-1",))
        ep = ep.add_evidence("repair-1").begin_verification().record_verification("verify-2").begin_review().record_review("review-2")
        ep = ep.complete(outcome="passed", regression_ids=("reg-2",))
        self.assertEqual(ep.repair_attempts, 1)

    def test_failure_produces_dont_rule(self):
        ep = self.episode().with_plan("plan-1").start_execution()
        failed = ep.fail(failure_class="flaky-test", dont_rules=("Do not use sleep for synchronization tests",))
        self.assertEqual(failed.phase.value, "failed")
        self.assertEqual(failed.dont_rules, ("Do not use sleep for synchronization tests",))

    def test_digest_and_parent_lineage(self):
        first = self.episode()
        second = first.with_plan("plan-1")
        self.assertEqual(second.parent_digest, first.digest)
        self.assertNotEqual(first.digest, second.digest)
        self.assertEqual(first.plan_digest, "")


if __name__ == "__main__":
    unittest.main()
