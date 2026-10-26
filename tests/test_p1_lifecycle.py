import json, tempfile, unittest
from pathlib import Path
import sys
ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'.ai-harness'))
import p1_lifecycle

class LifecycleTests(unittest.TestCase):
    def test_start_finish_produces_portable_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)
            p1_lifecycle.start(run,"fix export","prompt",{"tests":{"command":"pytest"}},{"mode":"implement"})
            manifest={"status":"completed","validation":{"passed":True},"git_diff":"src/export.py\n"}
            proof=p1_lifecycle.finish(run,manifest)
            for name in ("engineering-state.json","repository-dna.json","proof-bundle.json","proof-graph.json","regression-genome.json"):
                self.assertTrue((run/name).exists(),name)
            self.assertTrue(proof["proof_id"].startswith("proof-"))
            genome=json.loads((run/"regression-genome.json").read_text())
            self.assertEqual(genome["result"]["status"],"completed")

    def test_finish_records_candidate_regressions_without_auto_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)
            p1_lifecycle.start(run,"fix provider recovery","prompt",{"tests":{"command":"pytest"}},{"mode":"implement"})
            manifest={
                "status":"completed",
                "validation":{"passed":True},
                "git_diff":"portable/provider.py\n",
                "regression_candidates":[
                    {
                        "source_kind":"review_finding",
                        "source_ref":"evidence://review/42",
                        "trigger":"provider timeout leaves remote outcome unknown",
                        "expected":"block redispatch until provider lookup reconciles the outcome",
                        "constraints":["no duplicate dispatch","preserve receipt evidence"],
                    },
                    {
                        "source_kind":"user_correction",
                        "source_ref":"evidence://correction/7",
                        "trigger":"secret redaction regression",
                        "expected":"api_key=sk_live_abcdefghijklmnop",
                    },
                ],
                "proposer_id":"engineer-a",
            }
            p1_lifecycle.finish(run,manifest)
            genome=json.loads((run/"regression-genome.json").read_text())
            self.assertFalse(genome["promotion_policy"]["auto_promote"])
            self.assertEqual("candidate",genome["candidates"][0]["status"])
            self.assertFalse(genome["candidates"][0]["mandatory"])
            self.assertEqual(
                {"status":"rejected","reason":"candidate_validation_failed"},
                genome["candidates"][1],
            )

if __name__=="__main__":
    unittest.main()
