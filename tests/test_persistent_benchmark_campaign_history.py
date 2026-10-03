import tempfile
import unittest
from pathlib import Path

from portable.autonomy_benchmark import AutonomyBenchmarkGate
from portable.autonomy_benchmark_history import AutonomyBenchmarkHistory
from portable.evidence_backed_autonomy_benchmark import EpisodeEvidence, EvidenceBackedAutonomyBenchmark


class PersistentBenchmarkCampaignHistoryTests(unittest.TestCase):
    def test_campaign_episodes_preserve_domain_holdout_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            benchmark = EvidenceBackedAutonomyBenchmark(AutonomyBenchmarkGate()).evaluate((
                EpisodeEvidence("episode-1", True, 1, 1, True, 0.0, True, False, 0.9),
            ))
            history = AutonomyBenchmarkHistory(root)
            history.record(benchmark, task="task", evidence_ids=["episode-1:evidence"], domain="coding", holdout=True, episode_id="episode-1")
            rows = history.campaign_episodes()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].domain, "coding")
            self.assertTrue(rows[0].holdout)
            self.assertEqual(rows[0].evidence_ids, ("episode-1:evidence",))


if __name__ == "__main__":
    unittest.main()
