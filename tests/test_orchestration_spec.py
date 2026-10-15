import unittest

from portable.orchestration_spec import (
    OrchestrationSpec,
    OrchestrationTrial,
    WorkflowEdgeSpec,
    WorkflowNodeSpec,
    compare_orchestration_trials,
    assess_orchestration_promotion,
)


POLICY_DIGEST = "a" * 64
OUTCOME_A = "b" * 64
OUTCOME_B = "c" * 64
EVIDENCE = "d" * 64


def basic_spec(edges=()):
    return OrchestrationSpec(
        workflow_id="coding-task-v1",
        nodes=(
            WorkflowNodeSpec("plan", "agent", "repository.read", required_evidence=("plan",)),
            WorkflowNodeSpec("implement", "agent", "repository.write", side_effect=True, idempotent=True),
            WorkflowNodeSpec("verify", "evaluator", "test.run"),
        ),
        edges=tuple(edges) or (
            WorkflowEdgeSpec("plan", "implement"),
            WorkflowEdgeSpec("implement", "verify"),
        ),
        entry_nodes=("plan",),
        policy_digest=POLICY_DIGEST,
        max_steps=10,
        max_attempts=3,
        deadline_seconds=60,
    )


def trial(trial_id, framework, split, passed, outcome=OUTCOME_A, **overrides):
    values = dict(
        trial_id=trial_id, framework=framework, task_family="repo-repair",
        split=split, passed=passed, unauthorized_actions=0, trace_complete=True,
        recovery_expected=True, recovered=True, duration_ms=100, cost_usd=0.02,
        repeat_group="case-1", outcome_digest=outcome, evidence_digest=EVIDENCE,
    )
    values.update(overrides)
    return OrchestrationTrial(**values)


class OrchestrationSpecTests(unittest.TestCase):
    def test_spec_digest_is_stable_across_node_and_edge_order(self):
        first = basic_spec()
        second = OrchestrationSpec(
            workflow_id=first.workflow_id,
            nodes=tuple(reversed(first.nodes)),
            edges=tuple(reversed(first.edges)),
            entry_nodes=first.entry_nodes,
            policy_digest=first.policy_digest,
            max_steps=first.max_steps,
            max_attempts=first.max_attempts,
            deadline_seconds=first.deadline_seconds,
        )
        self.assertEqual(first.digest(), second.digest())

    def test_unbounded_cycles_are_rejected_and_bounded_cycles_are_allowed(self):
        unbounded = basic_spec((
            WorkflowEdgeSpec("plan", "implement"),
            WorkflowEdgeSpec("implement", "plan"),
            WorkflowEdgeSpec("implement", "verify"),
        ))
        with self.assertRaisesRegex(ValueError, "max_traversals"):
            unbounded.validate()
        bounded = basic_spec((
            WorkflowEdgeSpec("plan", "implement", max_traversals=2),
            WorkflowEdgeSpec("implement", "plan", max_traversals=2),
            WorkflowEdgeSpec("implement", "verify"),
        ))
        bounded.validate()

    def test_non_idempotent_side_effect_cannot_be_retried(self):
        spec = OrchestrationSpec(
            workflow_id="unsafe",
            nodes=(WorkflowNodeSpec("publish", "agent", "publish", max_attempts=2, side_effect=True),),
            edges=(), entry_nodes=("publish",), policy_digest=POLICY_DIGEST,
        )
        with self.assertRaisesRegex(ValueError, "non-idempotent side effect"):
            spec.validate()

    def test_mapping_round_trip_rejects_unknown_fields(self):
        spec = basic_spec()
        value = spec.as_dict()
        restored = OrchestrationSpec.from_mapping(value)
        self.assertEqual(spec.digest(), restored.digest())
        value["untrusted_extra"] = True
        with self.assertRaisesRegex(ValueError, "unknown or missing fields"):
            OrchestrationSpec.from_mapping(value)

    def test_comparison_requires_holdout_and_reports_reliability_metrics(self):
        rows = [
            trial("t1", "framework-a", "sealed_holdout", True, repeat_group="case-1", recovered=True),
            trial("t2", "framework-a", "sealed_holdout", True, repeat_group="case-1", recovered=None, recovery_expected=False),
            trial("t3", "framework-a", "sealed_holdout", False, outcome=OUTCOME_B, repeat_group="case-2", recovered=False),
            trial("t4", "framework-a", "sealed_holdout", False, outcome=OUTCOME_B, repeat_group="case-2", recovered=True),
        ]
        result = compare_orchestration_trials(rows)
        metrics = result["frameworks"]["framework-a"]
        self.assertEqual(metrics["holdout_pass_rate"], 0.5)
        self.assertEqual(metrics["unauthorized_action_count"], 0)
        self.assertEqual(metrics["recovery_success_rate"], 2 / 3)
        self.assertEqual(metrics["repeat_consistency_rate"], 1.0)
        self.assertIn("evaluator independence", result["limitations"][1])
        self.assertFalse(result["comparison_ready"])
        self.assertFalse(result["binding_consistency"]["workflow"])

    def test_comparison_rejects_missing_holdout_and_invalid_digests(self):
        with self.assertRaisesRegex(ValueError, "no sealed_holdout"):
            compare_orchestration_trials([trial("t1", "framework-a", "development", True)])
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            compare_orchestration_trials([trial("t2", "framework-a", "sealed_holdout", True, evidence_digest="not-a-digest")])


    def test_promotion_requires_exact_workflow_benchmark_policy_and_bounded_execution(self):
        workflow_digest = "1" * 64
        benchmark_digest = "2" * 64
        rows = [
            trial("p1", "candidate", "sealed_holdout", True, workflow_digest=workflow_digest, benchmark_digest=benchmark_digest, policy_digest=POLICY_DIGEST, termination_bounded=True),
            trial("p2", "candidate", "sealed_holdout", True, repeat_group="case-2", workflow_digest=workflow_digest, benchmark_digest=benchmark_digest, policy_digest=POLICY_DIGEST, termination_bounded=True),
        ]
        result = assess_orchestration_promotion(rows, framework="candidate", expected_workflow_digest=workflow_digest, expected_benchmark_digest=benchmark_digest, expected_policy_digest=POLICY_DIGEST)
        self.assertTrue(result["accepted"], result["reasons"])
        self.assertFalse(result["external_custody_verified"])
        self.assertFalse(result["sandbox_isolation_verified"])

    def test_promotion_fails_closed_on_policy_violation_and_unbounded_run(self):
        rows = [trial("p1", "candidate", "sealed_holdout", True, unauthorized_actions=1, workflow_digest="1" * 64, benchmark_digest="2" * 64, policy_digest=POLICY_DIGEST, termination_bounded=False)]
        result = assess_orchestration_promotion(rows, framework="candidate", expected_workflow_digest="1" * 64, expected_benchmark_digest="2" * 64, expected_policy_digest=POLICY_DIGEST)
        self.assertFalse(result["accepted"])
        self.assertIn("unauthorized_action_observed", result["reasons"])
        self.assertIn("termination_not_proven_bounded", result["reasons"])

    def test_promotion_rejects_wrong_benchmark_digest_and_low_holdout_rate(self):
        rows = [trial("p1", "candidate", "sealed_holdout", False, workflow_digest="1" * 64, benchmark_digest="3" * 64, policy_digest=POLICY_DIGEST, termination_bounded=True)]
        result = assess_orchestration_promotion(rows, framework="candidate", expected_workflow_digest="1" * 64, expected_benchmark_digest="2" * 64, expected_policy_digest=POLICY_DIGEST)
        self.assertFalse(result["accepted"])
        self.assertIn("benchmark_digest_mismatch_or_missing", result["reasons"])
        self.assertIn("holdout_threshold_not_met", result["reasons"])


if __name__ == "__main__":
    unittest.main()
