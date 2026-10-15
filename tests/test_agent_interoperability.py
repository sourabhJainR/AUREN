import unittest

from portable.agent_interoperability import (
    AgentContract,
    AdapterObservation,
    WorkflowContract,
    assess_adapter,
    compare_adapters,
)


def workflow():
    return WorkflowContract(
        workflow_id="coding-pr",
        agents=(
            AgentContract("planner", "plan", ("repository.read",)),
            AgentContract("builder", "implement", ("repository.write",)),
            AgentContract("reviewer", "review", ("repository.read",)),
        ),
        edges=(("planner", "builder"), ("builder", "reviewer")),
        allowed_actions=("read", "edit", "test"),
        required_evidence=("trace", "verification", "artifact_digest"),
        max_steps=40,
        max_attempts=5,
        deadline_seconds=120,
        max_cost_units=20,
    )


def observation(adapter="adapter-a", **overrides):
    values = dict(
        adapter=adapter,
        benchmark_digest="sealed-digest-1",
        case_ids=("happy-path", "crash-resume", "heldout-refactor"),
        passed_case_ids=("happy-path", "crash-resume", "heldout-refactor"),
        holdout_case_ids=("heldout-refactor",),
        passed_holdout_case_ids=("heldout-refactor",),
        authorization_enforced=True,
        bounded_termination=True,
        recovery_verified=True,
        evidence_refs=("trace", "verification", "artifact_digest"),
        duration_seconds=30,
        cost_units=4,
    )
    values.update(overrides)
    return AdapterObservation(**values)


class AgentInteroperabilityTests(unittest.TestCase):
    def test_workflow_contract_is_stable_and_versioned(self):
        spec = workflow()
        self.assertEqual(spec.as_dict()["spec_version"], "auren-agent-workflow-v1")
        self.assertEqual(spec.digest(), workflow().digest())

    def test_unknown_edges_and_duplicate_agents_fail_closed(self):
        invalid_edge = WorkflowContract(
            "bad", (AgentContract("planner", "plan", ("read",)),),
            (("planner", "missing"),), ("read",), ("trace",),
        )
        with self.assertRaisesRegex(ValueError, "known agents"):
            invalid_edge.validate()
        duplicate = WorkflowContract(
            "bad", (AgentContract("same", "plan", ("read",)), AgentContract("same", "build", ("write",))),
            (), ("read",), ("trace",),
        )
        with self.assertRaisesRegex(ValueError, "unique"):
            duplicate.validate()

    def test_hard_governance_gate_overrides_high_quality_score(self):
        result = assess_adapter(
            workflow(),
            observation(authorization_enforced=False),
            expected_benchmark_digest="sealed-digest-1",
        )
        self.assertFalse(result.accepted)
        self.assertIn("authorization_not_enforced", result.reasons)
        self.assertEqual(result.overall_pass_rate, 1.0)

    def test_mismatched_benchmark_and_missing_holdout_evidence_are_rejected(self):
        result = assess_adapter(
            workflow(),
            observation(benchmark_digest="different", passed_holdout_case_ids=()),
            expected_benchmark_digest="sealed-digest-1",
        )
        self.assertFalse(result.accepted)
        self.assertIn("benchmark_mismatch", result.reasons)
        self.assertIn("holdout_threshold_not_met", result.reasons)

    def test_cost_deadline_and_unauthorized_action_are_hard_gates(self):
        result = assess_adapter(
            workflow(),
            observation(duration_seconds=121, cost_units=21, unauthorized_action_count=1),
            expected_benchmark_digest="sealed-digest-1",
        )
        self.assertFalse(result.accepted)
        self.assertTrue({"deadline_exceeded", "cost_budget_exceeded", "unauthorized_actions_observed"}.issubset(result.reasons))

    def test_comparison_runs_same_digest_and_sorts_accepted_first(self):
        results = compare_adapters(
            workflow(),
            [
                observation("unsafe", authorization_enforced=False),
                observation("cheap", duration_seconds=10, cost_units=1),
                observation("slow", duration_seconds=60, cost_units=8),
            ],
            expected_benchmark_digest="sealed-digest-1",
        )
        self.assertEqual([result.adapter for result in results], ["cheap", "slow", "unsafe"])
        self.assertTrue(results[0].accepted)
        self.assertFalse(results[-1].accepted)

    def test_duplicate_adapter_names_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "adapter names"):
            compare_adapters(
                workflow(), [observation("same"), observation("same")],
                expected_benchmark_digest="sealed-digest-1",
            )

    def test_observation_rejects_non_finite_cost(self):
        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            assess_adapter(
                workflow(), observation(cost_units=float("nan")),
                expected_benchmark_digest="sealed-digest-1",
            )

    def test_missing_required_evidence_prevents_acceptance(self):
        result = assess_adapter(
            workflow(), observation(evidence_refs=("trace",)),
            expected_benchmark_digest="sealed-digest-1",
        )
        self.assertFalse(result.accepted)
        self.assertIn("required_evidence_missing:artifact_digest,verification", result.reasons)


if __name__ == "__main__":
    unittest.main()
