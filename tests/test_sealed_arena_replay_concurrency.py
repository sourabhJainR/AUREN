from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path

from portable.sealed_arena_replay import ReplayRejected, SealedArenaReplayLedger


class SealedArenaReplayConcurrencyTests(unittest.TestCase):
    def test_only_one_concurrent_claim_of_a_nonce_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "replay.sqlite3"

            def claim(_index):
                ledger = SealedArenaReplayLedger(ledger_path)
                try:
                    ledger.claim(
                        run_nonce="single-use-nonce",
                        campaign_digest="campaign",
                        corpus_digest="corpus",
                    )
                    return "accepted"
                except ReplayRejected:
                    return "rejected"

            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(claim, range(8)))

            self.assertEqual(outcomes.count("accepted"), 1)
            self.assertEqual(outcomes.count("rejected"), 7)

    def test_nonce_cannot_be_reused_for_a_different_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = SealedArenaReplayLedger(Path(directory) / "replay.sqlite3")
            ledger.claim(run_nonce="nonce", campaign_digest="campaign", corpus_digest="corpus-a")
            with self.assertRaises(ReplayRejected):
                ledger.claim(run_nonce="nonce", campaign_digest="campaign", corpus_digest="corpus-b")


if __name__ == "__main__":
    unittest.main()
