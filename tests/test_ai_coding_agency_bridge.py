import tempfile
import unittest
from pathlib import Path

from portable.agency_adaptive_planning import BenchmarkHistory, BenchmarkObservation
from portable.agency_artifact_regression import snapshot_artifact
from portable.agency_multi_specialist import SpecialistResourceProfile
from portable.ai_coding_agency_bridge import CodingTask, run_coding_task, verify_provenance
from portable.action_governance import ActionGovernanceGateway, ActionPolicy
from portable.side_effect_ledger import DurableSideEffectLedger
from portable.execution_boundary import ExecutionAuthorityBinding
from portable.runtime_recovery import RuntimeExecutionGuard
from portable.engineering_personas import AgentPersona, EngineeringPlan, PersonaReview, PersonaRoster, PersonaWorkflow


class AICodingAgencyBridgeTests(unittest.TestCase):
    def _worker(self, task, assignments):
        return {
            "deliverables": ["implemented change"],
            "verification": ["focused tests passed"],
            "dimensions": {
                "correctness": 25,
                "completeness": 15,
                "evidence": 15,
                "verification": 15,
                "scope_discipline": 10,
                "security_and_safety": 10,
                "clarity": 5,
                "maintainability": 5,
            },
            "hard_gates": {
                "acceptance_criteria_satisfied": True,
                "no_unresolved_blocker_or_material_finding": True,
                "verification_performed": True,
                "material_claims_have_evidence": True,
                "no_unauthorized_scope_expansion": True,
                "no_security_or_permission_bypass": True,
                "artifact_usable": True,
            },
            "evidence": [{"source": "test", "claim": "focused tests passed"}],
        }

    def test_clean_task_is_releasable_and_has_plan(self):
        task = CodingTask("run-1", "implement API validation")
        history = BenchmarkHistory()
        result = run_coding_task(task, self._worker, benchmark_history=history)
        self.assertTrue(result.ready)
        self.assertEqual(result.release.status, "passed")
        self.assertGreaterEqual(len(result.execution_plan.waves), 1)
        self.assertEqual(len(history.observations), 1)
        self.assertIsNotNone(result.benchmark)
        self.assertTrue(any(e.event == "execution-plan" for e in result.provenance.records))
        self.assertTrue(any(e.event == "benchmark-observed" for e in result.provenance.records))
        verify_provenance(result)

    def test_resource_profile_is_applied_to_selected_specialists(self):
        task = CodingTask("run-2", "implement API validation")
        resources = {
            "builder": SpecialistResourceProfile("builder", write_paths=("src/a.py",), mutation_mode="bounded"),
        }
        result = run_coding_task(task, self._worker, resource_profiles=resources)
        self.assertIsNotNone(result.execution_plan)
        verify_provenance(result)

    def test_artifact_regression_still_fails_release_and_is_benchmarked(self):
        task = CodingTask("run-3", "implement API validation")
        history = BenchmarkHistory()
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.txt"
            current = Path(tmp) / "current.txt"
            baseline.write_text("before", encoding="utf-8")
            current.write_text("after", encoding="utf-8")
            result = run_coding_task(
                task,
                self._worker,
                benchmark_history=history,
                baseline_artifacts=[snapshot_artifact(baseline)],
                current_artifacts=[snapshot_artifact(current)],
            )
        self.assertFalse(result.ready)
        self.assertEqual(result.regression.status, "failed")
        self.assertEqual(result.release.status, "failed")
        self.assertEqual(history.observations[0].regression_status, "failed")

    def test_prior_failures_adapt_next_run_to_read_only(self):
        history = BenchmarkHistory([
            BenchmarkObservation(
                task_id=f"old-{i}",
                plan_digest=f"digest-{i}",
                provenance_head=f"head-{i}",
                release_status="failed",
                regression_status="failed",
                quality_score=70,
                wave_count=2,
                conflict_count=1,
                blocked_count=0,
            )
            for i in range(3)
        ])
        task = CodingTask("run-4", "implement API validation", mutation_mode="bounded", support_limit=2)
        result = run_coding_task(task, self._worker, benchmark_history=history)
        self.assertEqual(result.adaptive_recommendation.mutation_mode, "read-only")
        self.assertEqual(result.execution.task.mutation_mode, "read-only")
        self.assertEqual(len(result.execution.assignments), 1)
        verify_provenance(result)



    def test_governance_gateway_blocks_mutation_without_explicit_write_scope(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        task = CodingTask("run-governed-1", "implement API validation", mutation_mode="bounded")
        calls = []
        result = run_coding_task(
            task, lambda profile, assignments: calls.append("worker") or self._worker(profile, assignments),
            governance_gateway=gateway, run_id="governed-bridge-1",
        )
        self.assertFalse(result.ready)
        self.assertEqual(calls, [])
        self.assertTrue(any(f.message == "action_governance:explicit_write_scope_missing" for f in result.execution.findings))
        self.assertFalse(result.execution.hard_gates["action_governance"])

    def test_governance_gateway_allows_explicit_scoped_coding_task(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
            protected_targets=("main", ".github/workflows"),
        ))
        task = CodingTask(
            "run-governed-2", "implement API validation",
            mutation_mode="bounded", allowed_artifact_changes=("src/api.py",),
        )
        calls = []
        result = run_coding_task(
            task, lambda profile, assignments: calls.append("worker") or self._worker(profile, assignments),
            governance_gateway=gateway, run_id="governed-bridge-2",
        )
        self.assertTrue(result.ready)
        self.assertEqual(calls, ["worker"])
        self.assertTrue(gateway.verify_trace()["passed"])
        self.assertIn("action_completed", [event["event"] for event in gateway.events])

    def _authority(self, task_id, run_id, **overrides):
        values = dict(
            execution_id="exec-" + task_id,
            run_id=run_id,
            task_id=task_id,
            repository_digest="repo-digest",
            repository_revision="abc123",
            principal="auren-host",
            allowed_capabilities=("repository.write",),
            allowed_actions=("specialist.execute",),
            allowed_targets=("src",),
            budget_id="budget-1",
            max_attempts=3,
        )
        values.update(overrides)
        return ExecutionAuthorityBinding(**values)

    def test_authority_binding_gates_real_coding_worker_and_charges_task_budget(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        task = CodingTask(
            "run-bound-1", "implement API validation",
            mutation_mode="bounded", allowed_artifact_changes=("src/api.py",),
        )
        guard = RuntimeExecutionGuard(max_steps=10, max_attempts=3)
        calls = []
        result = run_coding_task(
            task, lambda profile, assignments: calls.append("worker") or self._worker(profile, assignments),
            governance_gateway=gateway, run_id="governed-bound-1",
            execution_authority=self._authority(task.task_id, "governed-bound-1"),
            repository_revision="abc123", repository_digest="repo-digest",
            budget_id="budget-1", execution_guard=guard,
        )
        self.assertTrue(result.ready)
        self.assertEqual(calls, ["worker"])
        self.assertEqual(guard.attempts_used, 1)
        self.assertTrue(gateway.verify_trace()["passed"])

    def test_authority_binding_blocks_coding_worker_outside_allowed_targets(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        task = CodingTask(
            "run-bound-2", "implement API validation",
            mutation_mode="bounded", allowed_artifact_changes=("src/api.py",),
        )
        guard = RuntimeExecutionGuard(max_steps=10, max_attempts=3)
        calls = []
        result = run_coding_task(
            task, lambda profile, assignments: calls.append("worker") or self._worker(profile, assignments),
            governance_gateway=gateway, run_id="governed-bound-2",
            execution_authority=self._authority(task.task_id, "governed-bound-2", allowed_targets=("docs",)),
            repository_revision="abc123", repository_digest="repo-digest",
            budget_id="budget-1", execution_guard=guard,
        )
        self.assertFalse(result.ready)
        self.assertEqual(calls, [])
        self.assertEqual(guard.attempts_used, 0)
        self.assertTrue(any("authority_binding:target_out_of_scope" in f.message for f in result.execution.findings))

    def test_authority_bound_read_only_task_requires_explicit_read_paths(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        task = CodingTask("run-bound-read-1", "inspect API", mutation_mode="read-only")
        guard = RuntimeExecutionGuard(max_steps=10, max_attempts=3)
        calls = []
        result = run_coding_task(
            task, lambda profile, assignments: calls.append("worker") or self._worker(profile, assignments),
            governance_gateway=gateway, run_id="governed-bound-read-1",
            execution_authority=self._authority(
                task.task_id, "governed-bound-read-1",
                allowed_capabilities=("repository.read",),
            ),
            repository_revision="abc123", repository_digest="repo-digest",
            budget_id="budget-1", execution_guard=guard,
        )
        self.assertFalse(result.ready)
        self.assertEqual(calls, [])
        self.assertTrue(any("explicit_read_scope_missing" in f.message for f in result.execution.findings))

    def test_authority_binding_requires_host_revision_digest_and_task_guard(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        task = CodingTask(
            "run-bound-3", "implement API validation",
            mutation_mode="bounded", allowed_artifact_changes=("src/api.py",),
        )
        with self.assertRaisesRegex(ValueError, "authority-bound coding execution requires"):
            run_coding_task(
                task, self._worker, governance_gateway=gateway, run_id="governed-bound-3",
                execution_authority=self._authority(task.task_id, "governed-bound-3"),
            )

    def test_governance_gateway_blocks_task_protected_path(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        task = CodingTask(
            "run-governed-3", "edit secret config",
            mutation_mode="bounded", allowed_artifact_changes=("src/secrets.py",),
            protected_paths=("src",),
        )
        calls = []
        result = run_coding_task(
            task, lambda profile, assignments: calls.append("worker") or self._worker(profile, assignments),
            governance_gateway=gateway, run_id="governed-bridge-3",
        )
        self.assertFalse(result.ready)
        self.assertEqual(calls, [])
        self.assertTrue(any(f.message == "action_governance:task_protected_path" for f in result.execution.findings))

    def test_governance_gateway_requires_explicit_run_id(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        with self.assertRaisesRegex(ValueError, "run_id is required"):
            run_coding_task(CodingTask("run-governed-4", "read API", mutation_mode="read-only"),
                            self._worker, governance_gateway=gateway)


    def test_high_risk_governed_coding_requires_approval(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        task = CodingTask(
            "run-governed-5", "modify authentication flow",
            risk="critical", mutation_mode="bounded",
            allowed_artifact_changes=("src/auth.py",),
        )
        calls = []
        roster = PersonaRoster(
            planner=AgentPersona("planner", "planner-5", "systems-planner"),
            plan_reviewer=AgentPersona("plan_reviewer", "plan-reviewer-5", "adversarial-plan-reviewer"),
            implementer=AgentPersona("implementer", "implementer-5", "focused-code-implementer"),
            implementation_reviewer=AgentPersona("implementation_reviewer", "implementation-reviewer-5", "independent-security-reviewer"),
        )
        workflow = PersonaWorkflow(
            roster=roster,
            plan=lambda profile, assignments: EngineeringPlan(
                goal=profile.request,
                scope=("authentication flow",),
                assumptions=("write authorization must be enforced",),
                acceptance_criteria=("no worker runs without approval",),
                dependencies=("action governance gateway",),
                alternatives_considered=("deny without approval",),
                risks_and_mitigations=("unauthorized mutation; fail closed",),
                test_strategy=("governance regression test",),
                rollback_plan=("no write is dispatched before approval",),
                budget={"max_attempts": 1},
                source_revision=profile.source_revision,
                decision_rationale="Preserve the authority boundary with the smallest change.",
                selected_specialists=tuple(assignment.specialist for assignment in assignments[:1]),
            ),
            review_plan=lambda profile, reviewed: PersonaReview(
                reviewer_id="plan-reviewer-5", target_id="planner-5", approved=True,
                evidence_refs=("plan-review:governance",), dimensions={"completeness": 0.95},
            ),
            review_implementation=lambda profile, reviewed, artifact: PersonaReview(
                reviewer_id="implementation-reviewer-5", target_id="implementer-5",
                approved=not artifact.get("deliverables") and artifact.get("hard_gates", {}).get("action_governance") is False,
                findings=() if not artifact.get("deliverables") and artifact.get("hard_gates", {}).get("action_governance") is False else ("unexpected worker execution",),
                evidence_refs=("governance:denial",),
                dimensions={"authority": 1.0, "safety": 1.0},
            ),
        )
        result = run_coding_task(
            task, lambda profile, assignments: calls.append("worker") or self._worker(profile, assignments),
            governance_gateway=gateway, run_id="governed-bridge-5",
            repository_revision="test-revision", persona_workflow=workflow,
        )
        self.assertFalse(result.ready)
        self.assertEqual(calls, [])
        self.assertTrue(any(f.message == "action_governance:trusted_approval_missing_or_invalid" for f in result.execution.findings))



    def test_governance_gateway_rejects_unsafe_write_scopes(self):
        unsafe_scopes = (
            "../.github/workflows/build.yml",
            "C:\\repo\\src\\api.py",
            "src/../.github/workflows/build.yml",
            "src/" + chr(0) + "api.py",
        )
        for index, unsafe_scope in enumerate(unsafe_scopes):
            with self.subTest(scope=repr(unsafe_scope)):
                gateway = ActionGovernanceGateway(ActionPolicy(
                    allowed_capabilities=frozenset({"repository.read", "repository.write"}),
                ))
                task = CodingTask(
                    f"run-governed-unsafe-{index}", "edit source",
                    mutation_mode="bounded",
                    allowed_artifact_changes=(unsafe_scope,),
                )
                calls = []
                result = run_coding_task(
                    task, lambda profile, assignments: calls.append("worker") or self._worker(profile, assignments),
                    governance_gateway=gateway, run_id=f"governed-unsafe-{index}",
                )
                self.assertFalse(result.ready)
                self.assertEqual(calls, [])
                self.assertTrue(any(
                    f.message.startswith("action_governance:invalid_write_scope:")
                    for f in result.execution.findings
                ))


    def test_governed_worker_request_binds_every_preflighted_path(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        captured_requests = []
        original_execute = gateway.execute

        def capture_execute(request, handler, **kwargs):
            captured_requests.append(request)
            return original_execute(request, handler, **kwargs)

        gateway.execute = capture_execute
        task = CodingTask(
            "run-governed-multi-scope", "implement API and tests",
            mutation_mode="bounded",
            allowed_artifact_changes=("src/api.py", "tests/test_api.py"),
        )
        calls = []
        result = run_coding_task(
            task, lambda profile, assignments: calls.append("worker") or self._worker(profile, assignments),
            governance_gateway=gateway, run_id="governed-multi-scope",
        )
        self.assertTrue(result.ready)
        self.assertEqual(calls, ["worker"])
        self.assertEqual(len(captured_requests), 1)
        self.assertEqual(
            captured_requests[0].evidence_refs,
            ("scope:src/api.py", "scope:tests/test_api.py"),
        )
        self.assertEqual(captured_requests[0].action_id, "run-governed-multi-scope:coding-worker")
        self.assertTrue(gateway.verify_trace()["passed"])


    def test_legacy_coding_worker_path_blocks_external_effect_even_with_durable_ledger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = DurableSideEffectLedger(Path(temp_dir) / "side-effects.sqlite")
            gateway = ActionGovernanceGateway(ActionPolicy(
                allowed_capabilities=frozenset({"repository.read", "repository.write"}),
                external_effect_actions=frozenset({"specialist.execute"}),
                require_idempotency_for_external_effects=False,
            ), side_effect_ledger=ledger)
            task = CodingTask(
                "run-legacy-external-bound", "publish a consequential change",
                mutation_mode="bounded", allowed_artifact_changes=("src/api.py",),
            )
            calls = []
            result = run_coding_task(
                task, lambda profile, assignments: calls.append("worker") or self._worker(profile, assignments),
                governance_gateway=gateway, run_id="legacy-external-bound-run",
            )
            self.assertFalse(result.ready)
            self.assertEqual(calls, [])
            self.assertTrue(any(
                "execution_authority_binding_required" in finding.message
                for finding in result.execution.findings
            ))
            self.assertIn("unbound_external_effect_blocked", [event["event"] for event in gateway.events])
            self.assertTrue(gateway.verify_trace()["passed"])

    def test_legacy_coding_worker_path_blocks_external_effect_without_durable_ledger(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
            external_effect_actions=frozenset({"specialist.execute"}),
            require_idempotency_for_external_effects=False,
        ))
        task = CodingTask(
            "run-legacy-external", "publish a consequential change",
            mutation_mode="bounded", allowed_artifact_changes=("src/api.py",),
        )
        calls = []
        result = run_coding_task(
            task, lambda profile, assignments: calls.append("worker") or self._worker(profile, assignments),
            governance_gateway=gateway, run_id="legacy-external-run",
        )
        self.assertFalse(result.ready)
        self.assertEqual(calls, [])
        self.assertFalse(result.execution.hard_gates["action_governance"])
        self.assertTrue(any(
            "durable_idempotency_ledger_required" in finding.message
            for finding in result.execution.findings
        ))


if __name__ == "__main__":
    unittest.main()
