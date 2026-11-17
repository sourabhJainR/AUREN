import unittest

from benchmarks.paired_engineering_benchmark import (
    BenchmarkManifest, BenchmarkRun, compare_pair, summarize_pairs,
)


def manifest():
    return BenchmarkManifest(
        benchmark_id="auren-vs-direct", version="1.0.0",
        task_suite_digest="suite-sha", task_family="repository-engineering",
        model_tier="high", model_id="model-pinned", provider_version="provider-v1",
        context_budget_tokens=32000, acceptance_digest="acceptance-sha",
        rubric_digest="rubric-sha",
    )


def run(mode, suffix, **overrides):
    values = dict(
        run_id=mode + "-" + suffix, task_id="task-1", task_digest="task-sha",
        mode=mode, manifest_digest=manifest().digest, executor_id=mode + "-executor",
        evaluator_id=mode + "-independent-qa", evaluator_version="qa-v1",
        rubric_receipt_digest="receipt-" + mode, rubric_status="passed",
        metrics={
            "elapsed_seconds": 100, "active_user_seconds": 20,
            "human_interventions": 2, "input_tokens": 1000, "output_tokens": 200,
            "tool_calls": 4, "retries": 0, "policy_violations": 0,
            "escaped_defects": 0, "requirement_coverage": 100,
            "evidence_completeness": 100, "quality_score": 95,
            "provider_cost": 0.02,
        },
        recovery_success=True, duplicate_or_stale_side_effects=0,
        evidence_refs=("run://" + mode + "/" + suffix,),
    )
    values.update(overrides)
    return BenchmarkRun(**values)


class PairedEngineeringBenchmarkTests(unittest.TestCase):
    def test_matched_pair_reports_deltas_and_gates_promotion(self):
        baseline = run("direct", "1")
        candidate = run("auren", "1", metrics={
            **run("auren", "template").metrics,
            "elapsed_seconds": 80, "input_tokens": 700, "quality_score": 96,
        })
        result = compare_pair(manifest(), baseline=baseline, candidate=candidate)
        self.assertEqual(-20.0, result["deltas_candidate_minus_baseline"]["elapsed_seconds"])
        self.assertEqual(-300.0, result["deltas_candidate_minus_baseline"]["input_tokens"])
        self.assertTrue(result["promotion_eligible"])

    def test_pair_rejects_task_or_manifest_mismatch(self):
        baseline = run("direct", "1")
        with self.assertRaises(ValueError):
            compare_pair(manifest(), baseline=baseline,
                         candidate=run("auren", "1", task_digest="other"))
        other = BenchmarkManifest(
            "other", "1", "suite", "family", "high", "model", "provider",
            1000, "acceptance", "rubric",
        )
        with self.assertRaises(ValueError):
            compare_pair(other, baseline=baseline, candidate=run("auren", "1"))

    def test_missing_quality_or_recovery_evidence_blocks_promotion(self):
        baseline = run("direct", "1")
        candidate = run("auren", "1", recovery_success=None)
        result = compare_pair(manifest(), baseline=baseline, candidate=candidate)
        self.assertFalse(result["promotion_eligible"])

    def test_policy_regression_blocks_promotion_even_if_quality_improves(self):
        baseline = run("direct", "1")
        candidate = run("auren", "1", metrics={
            **run("auren", "template").metrics, "quality_score": 100, "policy_violations": 1,
        })
        result = compare_pair(manifest(), baseline=baseline, candidate=candidate)
        self.assertFalse(result["promotion_eligible"])
        self.assertFalse(result["safety_non_regression"])

    def test_self_evaluation_is_rejected_and_unscored_runs_are_retained_as_failures(self):
        with self.assertRaises(ValueError):
            run("direct", "1", evaluator_id="direct-executor")
        baseline = run("direct", "1")
        candidate = run("auren", "1", rubric_status="unscored")
        pair = compare_pair(manifest(), baseline=baseline, candidate=candidate)
        self.assertFalse(pair["comparable"])
        self.assertFalse(pair["promotion_eligible"])
        report = summarize_pairs(manifest(), [pair])
        self.assertEqual(1, report["failed_or_unscored_pairs"])
        self.assertEqual(0, report["comparable_pairs"])

    def test_failed_and_blocked_runs_are_counted_without_entering_comparable_metrics(self):
        baseline = run("direct", "1")
        failed = run("auren", "1", failed=True)
        blocked_baseline = run("direct", "2", task_id="task-2")
        blocked = run("auren", "2", task_id="task-2", rubric_status="blocked")
        failed_pair = compare_pair(manifest(), baseline=baseline, candidate=failed)
        blocked_pair = compare_pair(manifest(), baseline=blocked_baseline, candidate=blocked)
        report = summarize_pairs(manifest(), [failed_pair, blocked_pair])
        self.assertEqual(2, report["sample_size"])
        self.assertEqual(0, report["comparable_pairs"])
        self.assertEqual(2, report["failed_or_unscored_pairs"])
        self.assertIsNone(report["mean_deltas_candidate_minus_baseline"]["elapsed_seconds"])

    def test_campaign_allows_multiple_independent_pairs_for_the_same_task(self):
        first = compare_pair(manifest(), baseline=run("direct", "1"), candidate=run("auren", "1"))
        second = compare_pair(manifest(), baseline=run("direct", "2"), candidate=run("auren", "2"))
        report = summarize_pairs(manifest(), [first, second])
        self.assertEqual(2, report["sample_size"])
        self.assertEqual(1, report["unique_task_count"])
        self.assertEqual(2, report["comparable_pairs"])

    def test_campaign_rejects_reuse_of_the_same_run_receipt(self):
        first = compare_pair(manifest(), baseline=run("direct", "1"), candidate=run("auren", "1"))
        duplicate = compare_pair(manifest(), baseline=run("direct", "1"), candidate=run("auren", "2"))
        with self.assertRaisesRegex(ValueError, "cannot reuse"):
            summarize_pairs(manifest(), [first, duplicate])

    def test_cost_remains_unknown_and_report_marks_small_sample(self):
        baseline = run("direct", "1", metrics={**run("direct", "template").metrics, "provider_cost": None})
        candidate = run("auren", "1", metrics={**run("auren", "template").metrics, "provider_cost": None})
        pair = compare_pair(manifest(), baseline=baseline, candidate=candidate)
        report = summarize_pairs(manifest(), [pair])
        self.assertIsNone(report["provider_cost_delta_mean"])
        self.assertEqual("descriptive_only_small_sample", report["uncertainty"])
        self.assertEqual(1, report["sample_size"])


if __name__ == "__main__":
    unittest.main()
