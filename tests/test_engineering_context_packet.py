from __future__ import annotations

import unittest

from portable.engineering_context_packet import build_context_packet
from portable.engineering_plan import EngineeringPlan, EngineeringPlanRun, PlanTask, new_plan_run


def sample_plan() -> EngineeringPlan:
    return EngineeringPlan(
        run_id="context-run", goal="Implement one bounded feature",
        requirements=("Feature behavior is specified", "Regression behavior is preserved"),
        source_revision="revision-123",
        tasks=(
            PlanTask("design", "Design feature", ("design reviewed",), "revert design"),
            PlanTask(
                "implement", "Implement feature", ("tests pass", "security checks pass"),
                "revert implementation", depends_on=("design",), risk="high",
            ),
        ),
    )


def ready_run() -> tuple[EngineeringPlan, EngineeringPlanRun]:
    plan = sample_plan()
    run = new_plan_run(plan)
    run = run.transition_task("design", "ready", plan=plan, reason="no dependencies")
    run = run.transition_task("design", "running", plan=plan, reason="started")
    run = run.transition_task("design", "verifying", plan=plan, reason="work done")
    run = run.transition_task(
        "design", "passed", plan=plan, reason="verified", evidence_refs=("ci://run/123",),
    )
    run = run.transition_task("implement", "ready", plan=plan, reason="dependency passed")
    return plan, run


class EngineeringContextPacketTests(unittest.TestCase):
    def build(self, **overrides):
        plan, run = ready_run()
        args = dict(
            plan=plan, run=run, task_id="implement",
            policy_constraints=("No secrets in worker context", "Do not weaken security or approval gates"),
            approvals=("high-risk implementation approved by authorized reviewer",),
            budget_remaining={"attempts": 4, "steps": 12, "tool_calls": 8, "tokens": 4000},
            next_safe_action="Inspect the target module and implement only the acceptance criteria",
            unresolved_questions=("Confirm whether the legacy adapter is still supported",),
            prior_failure_summaries=("Previous approach failed due to stale checkpoint",),
            conventions=("Use repository standard test patterns",),
            codebase_map_refs=("repo://portable/target.py#L20-L80",),
            optional_context=("Unrelated historical discussion", "Extra background note"),
        )
        args.update(overrides)
        return build_context_packet(**args)

    def test_packet_is_deterministic_and_integrity_verifiable(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first.packet_digest, second.packet_digest)
        first.verify()
        self.assertEqual("revision-123", first.source_revision)
        self.assertEqual("passed", first.dependency_status["design"])
        self.assertIn("No secrets in worker context", first.policy_constraints)
        self.assertIn("revert implementation", first.rollback_plan)

    def test_optional_context_is_pruned_before_mandatory_contract(self):
        packet = self.build(max_bytes=1410)
        packet.verify()
        self.assertTrue(packet.policy_constraints)
        self.assertTrue(packet.approvals)
        self.assertTrue(packet.rollback_plan)
        self.assertTrue(packet.acceptance_criteria)
        self.assertTrue(packet.next_safe_action)
        self.assertLessEqual(packet.serialized_size, 1410)
        self.assertLess(len(packet.optional_context), 2)

    def test_oversized_mandatory_context_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "mandatory context packet exceeds"):
            self.build(
                max_bytes=512,
                policy_constraints=("mandatory policy " + ("x" * 1000),),
                approvals=(), unresolved_questions=(), prior_failure_summaries=(),
                conventions=(), codebase_map_refs=(), optional_context=(),
            )

    def test_packet_rejects_missing_policy_and_credential_like_budget_keys(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            self.build(policy_constraints=())
        with self.assertRaisesRegex(ValueError, "credential-like"):
            self.build(budget_remaining={"api_key": 1})

    def test_packet_rejects_changed_plan_or_terminal_task(self):
        plan, run = ready_run()
        changed = EngineeringPlan(
            run_id=plan.run_id, goal=plan.goal, requirements=("changed",),
            source_revision=plan.source_revision, tasks=plan.tasks,
        )
        with self.assertRaisesRegex(ValueError, "plan digest mismatch"):
            build_context_packet(
                plan=changed, run=run, task_id="implement",
                policy_constraints=("policy",), budget_remaining={"steps": 1},
                next_safe_action="continue",
            )
        passed = run.transition_task(
            "implement", "running", plan=plan, reason="started",
        ).transition_task(
            "implement", "verifying", plan=plan, reason="done",
        ).transition_task(
            "implement", "passed", plan=plan, reason="verified", evidence_refs=("ci://pass",),
        )
        with self.assertRaisesRegex(ValueError, "terminal task"):
            build_context_packet(
                plan=plan, run=passed, task_id="implement",
                policy_constraints=("policy",), budget_remaining={"steps": 1},
                next_safe_action="continue",
            )

    def test_ready_task_cannot_run_with_incomplete_dependency(self):
        plan = sample_plan()
        run = new_plan_run(plan)
        run = run.transition_task("design", "ready", plan=plan, reason="ready")
        with self.assertRaisesRegex(ValueError, "incomplete dependencies"):
            build_context_packet(
                plan=plan, run=run, task_id="implement",
                policy_constraints=("policy",), budget_remaining={"steps": 1},
                next_safe_action="implement",
            )

    def test_worker_packet_does_not_allow_credentials_in_context_keys(self):
        with self.assertRaisesRegex(ValueError, "credential-like"):
            self.build(budget_remaining={"access-token": 2})


if __name__ == "__main__":
    unittest.main()
