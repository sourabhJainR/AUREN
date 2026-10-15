import tempfile
import unittest
from pathlib import Path

from portable.agency_adaptive_planning import BenchmarkHistory, BenchmarkObservation
from portable.agency_artifact_regression import snapshot_artifact
from portable.agency_multi_specialist import SpecialistResourceProfile
from portable.ai_coding_agency_bridge import CodingTask, run_coding_task, verify_provenance
from portable.action_governance import ActionGovernanceGateway, ActionPolicy


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
        self.assertIn("action_governance:explicit_write_scope_missing", result.execution.findings)
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
        self.assertIn("action_governance:task_protected_path", result.execution.findings)

    def test_governance_gateway_requires_explicit_run_id(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        with self.assertRaisesRegex(ValueError, "run_id is required"):
            run_coding_task(CodingTask("run-governed-4", "read API", mutation_mode="read-only"),
                            self._worker, governance_gateway=gateway)

if __name__ == "__main__":
    unittest.main()
