from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from portable.active_learning import ActiveLearningController, CapabilityBelief, SelfModel


class ActiveLearningTests(unittest.TestCase):
    def test_cold_start_is_uncertain_and_proposes_probe(self):
        with TemporaryDirectory() as d:
            c=ActiveLearningController(Path(d))
            model=c.self_model(role="team",task="new task",capabilities=("web_search","terminal"))
            self.assertEqual(model.uncertainty,1.0)
            proposal=c.propose(self_model=model,candidates=("web_search","terminal"))
            self.assertIsNotNone(proposal)
            self.assertGreater(proposal.information_value,0.0)

    def test_known_capability_has_lower_uncertainty(self):
        with TemporaryDirectory() as d:
            c=ActiveLearningController(Path(d),minimum_samples=3)
            from portable.learning_steward import LearningSteward
            s=LearningSteward(Path(d),run_id="t",task="task")
            for i in range(8):
                s.record_experience(key="team:task:capability:terminal",outcome="passed",
                    evidence_quality=.95,cost_score=.1,duration_seconds=1,decision="verified",
                    evidence_ids=[str(i)])
            model=c.self_model(role="team",task="task",capabilities=("terminal",))
            self.assertEqual(model.strongest,"terminal")
            self.assertLess(model.uncertainty,1.0)
            self.assertLess(model.capabilities[0].uncertainty,0.5)


if __name__=="__main__":
    unittest.main()
