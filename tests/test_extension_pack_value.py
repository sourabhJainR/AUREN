from __future__ import annotations

import unittest

from portable.extension_pack_value import evaluate_extension_pack_value


BASELINE = "a" * 64
CANDIDATE = "b" * 64
CORPUS = "c" * 64


def runs(config: str, successes: set[int], *, tokens: int = 100, latency: int = 100,
         safety: set[int] | None = None, count: int = 20):
    safety = safety or set()
    return [
        {
            "task_id": f"task-{i:03d}",
            "config_digest": config,
            "corpus_digest": CORPUS,
            "success": i in successes,
            "input_tokens": tokens,
            "latency_ms": latency,
            "safety_violations": 1 if i in safety else 0,
            "evidence_ref": f"evidence://{config[:4]}/task-{i:03d}",
        }
        for i in range(count)
    ]


class ExtensionPackValueTests(unittest.TestCase):
    def evaluate(self, baseline=None, candidate=None, **kwargs):
        return evaluate_extension_pack_value(
            baseline_runs=baseline if baseline is not None else runs(BASELINE, set(range(10))),
            candidate_runs=candidate if candidate is not None else runs(CANDIDATE, set(range(18)), tokens=105, latency=110),
            baseline_digest=BASELINE,
            candidate_digest=CANDIDATE,
            corpus_digest=CORPUS,
            **kwargs,
        )

    def test_measured_uplift_recommends_but_does_not_activate_pack(self):
        result = self.evaluate()
        self.assertTrue(result["default_enable_recommended"], result["reason"])
        self.assertFalse(result["activation_performed"])
        self.assertFalse(result["trust_granted"])
        self.assertEqual(0.4, result["success_uplift"])
        self.assertTrue(all(result["checks"].values()))

    def test_insufficient_sample_fails_closed(self):
        baseline = runs(BASELINE, {0, 1, 2, 3, 4}, count=8)
        candidate = runs(CANDIDATE, set(range(8)), count=8)
        result = self.evaluate(baseline, candidate)
        self.assertFalse(result["default_enable_recommended"])
        self.assertFalse(result["checks"]["minimum_sample"])

    def test_no_measured_uplift_denies_default_enablement(self):
        baseline = runs(BASELINE, set(range(15)))
        candidate = runs(CANDIDATE, set(range(15)))
        result = self.evaluate(baseline, candidate)
        self.assertFalse(result["default_enable_recommended"])
        self.assertFalse(result["checks"]["minimum_success_uplift"])

    def test_safety_regression_denies_even_with_task_success_uplift(self):
        baseline = runs(BASELINE, set(range(10)))
        candidate = runs(CANDIDATE, set(range(18)), safety={0})
        result = self.evaluate(baseline, candidate)
        self.assertFalse(result["default_enable_recommended"])
        self.assertFalse(result["checks"]["no_candidate_safety_violations"])

    def test_cost_regression_denies_default_enablement(self):
        baseline = runs(BASELINE, set(range(10)))
        candidate = runs(CANDIDATE, set(range(18)), tokens=150)
        result = self.evaluate(baseline, candidate)
        self.assertFalse(result["default_enable_recommended"])
        self.assertFalse(result["checks"]["token_cost_within_budget"])

    def test_mismatched_task_sets_are_rejected(self):
        baseline = runs(BASELINE, set(range(10)))
        candidate = runs(CANDIDATE, set(range(18)))
        candidate.pop()
        with self.assertRaisesRegex(ValueError, "exactly the same task IDs"):
            self.evaluate(baseline, candidate)

    def test_missing_evidence_and_invalid_digest_are_rejected(self):
        candidate = runs(CANDIDATE, set(range(18)))
        candidate[0]["evidence_ref"] = ""
        with self.assertRaisesRegex(ValueError, "evidence reference"):
            self.evaluate(candidate=candidate)
        with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
            evaluate_extension_pack_value(
                baseline_runs=runs(BASELINE, set(range(10))),
                candidate_runs=runs(CANDIDATE, set(range(18))),
                baseline_digest="not-a-digest",
                candidate_digest=CANDIDATE,
                corpus_digest=CORPUS,
            )


if __name__ == "__main__":
    unittest.main()
