import unittest

from portable.evaluation_rubric import RubricCriterion, RubricDefinition, rubric_plan_binding
from portable.quality_optimizer_contract import (
    ProviderUsageReceipt,
    evaluate_verified_rubric,
    verify_paired_run_identity,
    verify_plan_rubric_binding,
)


D = "a" * 64
A = "b" * 64


class QualityOptimizerContractTests(unittest.TestCase):
    def setUp(self):
        self.rubric = RubricDefinition(
            rubric_id="verified-test",
            version="1.0.0",
            criteria=(RubricCriterion("tests", "Test evidence", 100, ("tests",)),),
        )

    def test_plan_binding_rejects_stale_rubric_or_plan(self):
        binding = rubric_plan_binding(
            self.rubric, plan_id="plan-1", plan_digest=D, task_ids=("task-1",)
        )
        verify_plan_rubric_binding(
            self.rubric, binding, plan_id="plan-1", plan_digest=D, task_ids=("task-1",)
        )
        with self.assertRaises(ValueError):
            verify_plan_rubric_binding(
                self.rubric, binding, plan_id="plan-1", plan_digest="c" * 64,
                task_ids=("task-1",),
            )

    def test_verified_rubric_rejects_missing_or_wrong_run_evidence(self):
        records = {
            "ev-1": {
                "evidence_id": "ev-1", "run_id": "run-1",
                "source_digest": D, "artifact_digest": A,
                "evidence_type": "tests", "verified": True,
                "custodian_id": "independent-custodian",
            }
        }
        kwargs = dict(
            subject_id="candidate", run_id="run-1", plan_digest=D,
            evaluator_version="qa-v1", scores={"tests": 100},
            evidence={"tests": ["ev-1"]}, hard_gates={}, rationales={"tests": "Verified"},
            executor_id="builder", evaluator_id="reviewer",
        )
        receipt = evaluate_verified_rubric(
            self.rubric, evidence_resolver=records.get,
            expected_run_id="run-1", expected_source_digest=D,
            expected_artifact_digests=(A,), **kwargs,
        )
        self.assertEqual("passed", receipt.status)
        with self.assertRaises(ValueError):
            evaluate_verified_rubric(
                self.rubric, evidence_resolver=records.get,
                expected_run_id="run-2", expected_source_digest=D,
                expected_artifact_digests=(A,), **kwargs,
            )

    def test_evaluator_alias_cannot_claim_independence(self):
        with self.assertRaisesRegex(ValueError, "same principal"):
            evaluate_verified_rubric(
                self.rubric, evidence_resolver=lambda _: None,
                expected_run_id="run-1", expected_source_digest=D,
                expected_artifact_digests=(A,), executor_id="QA-Team",
                evaluator_id=" qa-team ", subject_id="candidate", run_id="run-1",
                plan_digest=D, evaluator_version="qa-v1", scores={},
                evidence={}, hard_gates={},
            )

    def test_usage_receipt_requires_measured_valid_counters_and_price_revision(self):
        receipt = ProviderUsageReceipt(
            provider="provider", model="model", provider_version="v1", request_id="req-1",
            task_digest=D, context_digest=D, policy_digest=D, rubric_digest=D,
            input_tokens=100, output_tokens=20, cached_input_tokens=40,
            latency_ms=12.5, retry_count=0, tool_call_count=2,
        )
        self.assertEqual(120, receipt.total_tokens)
        self.assertIsNone(receipt.total_cost)
        self.assertEqual(64, len(receipt.receipt_digest))
        with self.assertRaises(ValueError):
            ProviderUsageReceipt(
                provider="p", model="m", provider_version="v1", request_id="r",
                task_digest=D, context_digest=D, policy_digest=D, rubric_digest=D,
                input_tokens=1, output_tokens=1, cached_input_tokens=2,
                latency_ms=1, retry_count=0, tool_call_count=0,
            )

    def test_paired_runs_require_exact_experiment_identity(self):
        baseline = {key: "same" for key in (
            "task_digest", "source_digest", "context_digest", "tool_digest",
            "provider_version", "model_tier", "model_id", "rubric_digest",
            "policy_digest", "privacy_scope_digest", "acceptance_digest",
        )}
        baseline["context_budget_tokens"] = 4096
        verify_paired_run_identity(baseline, dict(baseline))
        with self.assertRaises(ValueError):
            verify_paired_run_identity(baseline, dict(baseline, context_digest="changed"))


if __name__ == "__main__":
    unittest.main()
