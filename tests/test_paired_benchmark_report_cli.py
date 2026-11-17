import unittest

from scripts.report_paired_engineering_benchmark import build_report


class PairedBenchmarkReportCliTests(unittest.TestCase):
    def test_report_builder_rejects_malformed_pair_envelopes(self):
        with self.assertRaises(ValueError):
            build_report({"manifest": {}, "pairs": [{"baseline": {}, "candidate": {}, "extra": 1}]})

    def test_report_builder_uses_the_shared_pair_contract(self):
        manifest = {
            "benchmark_id": "auren-vs-direct", "version": "1.0.0",
            "task_suite_digest": "suite-sha", "task_family": "repository-engineering",
            "model_tier": "high", "model_id": "model-pinned",
            "provider_version": "provider-v1", "context_budget_tokens": 32000,
            "acceptance_digest": "acceptance-sha", "rubric_digest": "rubric-sha",
        }
        from benchmarks.paired_engineering_benchmark import BenchmarkManifest
        digest = BenchmarkManifest(**manifest).digest

        def run(mode):
            return {
                "run_id": mode + "-run", "task_id": "task-1", "task_digest": "task-sha",
                "mode": mode, "manifest_digest": digest, "executor_id": mode + "-executor",
                "evaluator_id": mode + "-qa", "evaluator_version": "qa-v1",
                "rubric_receipt_digest": mode + "-receipt", "rubric_status": "passed",
                "metrics": {
                    "elapsed_seconds": 100, "active_user_seconds": 20,
                    "human_interventions": 2, "input_tokens": 1000, "output_tokens": 200,
                    "tool_calls": 4, "retries": 0, "policy_violations": 0,
                    "escaped_defects": 0, "requirement_coverage": 100,
                    "evidence_completeness": 100, "quality_score": 95,
                    "provider_cost": None,
                },
                "recovery_success": True, "duplicate_or_stale_side_effects": 0,
                "evidence_refs": ["run://" + mode],
            }

        report = build_report({
            "manifest": manifest,
            "pairs": [{"baseline": run("direct"), "candidate": run("auren")}],
        })
        self.assertEqual(1, report["sample_size"])
        self.assertEqual("auren-paired-benchmark-report-v1", report["schema_version"])
        self.assertEqual(1, report["metric_sample_counts"]["elapsed_seconds"])
        self.assertEqual(1.0, report["metric_sample_coverage"]["elapsed_seconds"])
        self.assertEqual(0, report["metric_sample_counts"]["provider_cost"])


if __name__ == "__main__":
    unittest.main()
