import ast
import re
import unittest
from fnmatch import fnmatchcase
from pathlib import Path

from scripts.impact_policy import classify_changes, classify_path, final_status


class ImpactPolicyTests(unittest.TestCase):
    def test_every_recovery_sensitive_source_has_mandatory_test_suites(self):
        expected = {
            ".ai-harness/runtime/canary_evaluator.py": {"tests.test_canary_evaluator", "tests.test_production_evaluator_trust"},
            "portable/external_evaluation_campaign.py": {"tests.test_external_evaluation_campaign", "tests.test_external_evaluator_gateway"},
            "portable/independent_evaluation_attestation.py": {"tests.test_independent_evaluation_attestation", "tests.test_sealed_arena_boundary"},
            "portable/attestation_freshness.py": {"tests.test_attestation_freshness", "tests.test_postgres_attestation_replay_registry"},
            "portable/postgres_attestation_replay_registry.py": {"tests.test_postgres_attestation_replay_registry", "tests.test_attestation_freshness"},
            "tests/test_attestation_freshness.py": {"tests.test_attestation_freshness", "tests.test_postgres_attestation_replay_registry"},
            "tests/test_postgres_attestation_replay_registry.py": {"tests.test_postgres_attestation_replay_registry", "tests.test_attestation_freshness"},
            "tests/test_canary_evaluator.py": {"tests.test_canary_evaluator"},
            "tests/test_production_evaluator_trust.py": {"tests.test_production_evaluator_trust"},
            "tests/test_external_evaluation_campaign.py": {"tests.test_external_evaluation_campaign"},
            "tests/test_independent_evaluation_attestation.py": {"tests.test_independent_evaluation_attestation"},
            "portable/container_sandboxed_arena.py": {"tests.test_container_sandboxed_arena", "tests.test_container_sandboxed_arena_integration"},
            "portable/isolated_arena_runner.py": {"tests.test_isolated_arena_runner", "tests.test_independent_arena"},
            "portable/external_evaluator_gateway.py": {"tests.test_external_evaluator_gateway", "tests.test_external_evaluator_gateway_budget"},
            "portable/sealed_arena_boundary.py": {"tests.test_sealed_arena_boundary", "tests.test_sealed_arena_custody", "tests.test_sealed_arena_signature"},
            "portable/sealed_arena_exchange.py": {"tests.test_sealed_arena_exchange", "tests.test_external_evaluator_gateway"},
            "portable/sealed_arena_signature.py": {"tests.test_sealed_arena_signature", "tests.test_sealed_arena_custody"},
            "portable/sealed_corpus_provenance.py": {"tests.test_sealed_corpus_provenance", "tests.test_sealed_arena_boundary"},
            "portable/side_effect_ledger.py": {"tests.test_side_effect_ledger", "tests.test_provider_operation_lifecycle"},
            "portable/action_governance.py": {"tests.test_durable_approval"},
            "portable/governed_sandbox_execution.py": {"tests.test_governed_sandbox_execution", "tests.test_execution_boundary", "tests.test_durable_approval", "tests.test_side_effect_ledger"},
            "tests/test_governed_sandbox_execution.py": {"tests.test_governed_sandbox_execution", "tests.test_execution_boundary", "tests.test_side_effect_ledger"},
            "portable/ai_coding_agency_bridge.py": {"tests.test_ai_coding_agency_bridge", "tests.test_execution_boundary", "tests.test_durable_approval"},
            "tests/test_ai_coding_agency_bridge.py": {"tests.test_ai_coding_agency_bridge", "tests.test_execution_boundary"},
            "portable/agency_team_orchestrator.py": {"tests.test_agency_team_orchestrator", "tests.test_execution_boundary", "tests.test_durable_approval"},
            "tests/test_agency_team_orchestrator.py": {"tests.test_agency_team_orchestrator", "tests.test_execution_boundary"},
            "tests/test_execution_boundary.py": {"tests.test_execution_boundary", "tests.test_durable_approval", "tests.test_side_effect_ledger"},
            "portable/durable_approval.py": {"tests.test_durable_approval", "tests.test_side_effect_ledger", "tests.test_execution_controller"},
            "portable/provider_operation_lifecycle.py": {"tests.test_provider_operation_lifecycle", "tests.test_side_effect_ledger"},
            "portable/subprocess_provider.py": {"tests.test_subprocess_provider", "tests.test_bounded_subprocess_fault_injection"},
            "tests/test_subprocess_provider.py": {"tests.test_subprocess_provider"},
            "portable/ci_repair_watchdog.py": {"tests.test_ci_repair_watchdog", "tests.test_engineering_recovery"},
            "portable/continuous_execution.py": {"tests.test_continuous_execution", "tests.test_ci_repair_watchdog", "tests.test_engineering_recovery"},
            "portable/execution_controller.py": {"tests.test_execution_controller", "tests.test_execution_controller_restart", "tests.test_execution_controller_promotion_rollback_contract", "tests.test_provider_operation_lifecycle", "tests.test_durable_approval"},
            "tests/test_consequential_run_once_recovery.py": {"tests.test_consequential_run_once_recovery", "tests.test_execution_controller"},
            "tests/test_consequential_outer_receipt_crash.py": {"tests.test_consequential_outer_receipt_crash", "tests.test_consequential_run_once_recovery", "tests.test_execution_controller"},
            "portable/engineering_recovery.py": {"tests.test_engineering_recovery", "tests.test_engineering_recovery_fault_matrix"},
            "portable/engineering_plan_recovery.py": {"tests.test_engineering_plan_recovery"},
            "tests/test_engineering_plan_recovery.py": {"tests.test_engineering_plan_recovery"},
            "tests/test_durable_approval.py": {"tests.test_durable_approval", "tests.test_side_effect_ledger_crash_fault_injection"},
            "portable/progress_journal.py": {"tests.test_progress_journal", "tests.test_progress_journal_integration"},
            "portable/canonical_evidence_ledger.py": {"tests.test_canonical_evidence_ledger", "tests.test_provider_operation_lifecycle"},
            "portable/agency_evolution_gates.py": {"tests.test_agency_evolution_gates", "tests.test_promotion_evidence_chain", "tests.test_evidence_freshness"},
            "tests/test_agency_evolution_gates.py": {"tests.test_agency_evolution_gates"},
            "portable/vibe_coding_adapters.py": {"tests.test_vibe_coding_adapters", "tests.test_provider_operation_lifecycle", "tests.test_side_effect_ledger"},
            "portable/vibe_coding_host.py": {"tests.test_vibe_coding_host", "tests.test_vibe_coding_adapters"},
            "tests/test_vibe_coding_host.py": {"tests.test_vibe_coding_host"},
            "tests/test_vibe_coding_adapters.py": {"tests.test_vibe_coding_adapters"},
            "portable/engineering_evidence_capture.py": {"tests.test_engineering_evidence_capture", "tests.test_execution_controller_evidence_capture"},
            "portable/agency_observability.py": {"tests.test_agency_observability"},
            "tests/test_agency_observability.py": {"tests.test_agency_observability"},
            "portable/engineering_dashboard.py": {"tests.test_engineering_dashboard"},
            "tests/test_engineering_dashboard.py": {"tests.test_engineering_dashboard"},
            "tests/test_engineering_evidence_capture.py": {"tests.test_engineering_evidence_capture"},
            "tests/test_execution_controller_evidence_capture.py": {"tests.test_execution_controller_evidence_capture"},
            "auren_cli.py": {"tests.test_continuous_execution_cli", "tests.test_continuous_execution"},
            "portable/authority/policy.py": {"tests.test_security_gate"},
            "portable/rollback_controller.py": {"tests.test_state_root_rollback_rehearsal", "tests.test_execution_controller_promotion_rollback_contract", "tests.test_execution_controls"},
            ".ai-harness/security_gate.py": {"tests.test_security_gate"},
            ".ai-harness/runtime/rollback_controller.py": {"tests.test_execution_controls", "tests.test_state_root_rollback_rehearsal"},
        }
        for path, required in expected.items():
            with self.subTest(path=path):
                impact = classify_path(path)
                self.assertIsNotNone(impact)
                self.assertTrue(impact.hardening_required)
                self.assertTrue(required.issubset(set(impact.required_suites)))
        hardening_workflow = Path(".github/workflows/production-hardening.yml").read_text(encoding="utf-8")
        for path in expected:
            with self.subTest(hardening_suite_path=path):
                impact = classify_path(path)
                for suite in impact.required_suites:
                    self.assertIn(suite, hardening_workflow, f"{path} requires {suite}, but hardening does not run it")

    def test_runtime_resilience_filter_covers_recovery_sensitive_paths(self):
        workflow = Path(".github/workflows/runtime-resilience.yml").read_text(encoding="utf-8")
        match = re.search(r"patterns = (\[.*?\])", workflow, re.DOTALL)
        self.assertIsNotNone(match, "runtime resilience workflow must expose its changed-path policy")
        patterns = ast.literal_eval(match.group(1))
        required_paths = (
            "portable/provider_operation_lifecycle.py",
            "tests/test_provider_operation_lifecycle.py",
            "portable/action_governance.py",
            "portable/execution_boundary.py",
            "tests/test_execution_boundary.py",
            "portable/governed_sandbox_execution.py",
            "tests/test_governed_sandbox_execution.py",
            "portable/ai_coding_agency_bridge.py",
            "tests/test_ai_coding_agency_bridge.py",
            "portable/agency_team_orchestrator.py",
            "tests/test_agency_team_orchestrator.py",
            "scripts/impact_policy.py",
            "tests/test_impact_policy.py",
            "portable/subprocess_provider.py",
            "tests/test_subprocess_provider.py",
            "portable/side_effect_ledger.py",
            "tests/test_side_effect_ledger.py",
            "portable/ci_repair_watchdog.py",
            "tests/test_ci_repair_watchdog.py",
            "portable/engineering_recovery.py",
            "tests/test_engineering_recovery.py",
            "tests/test_engineering_recovery_fault_matrix.py",
            "portable/execution_controller.py",
            "tests/test_execution_controller_restart.py",
            "tests/test_consequential_outer_receipt_crash.py",
            "portable/durable_approval.py",
            "tests/test_durable_approval.py",
            "portable/vibe_coding_adapters.py",
            "tests/test_vibe_coding_adapters.py",
            "portable/vibe_coding_host.py",
            "tests/test_vibe_coding_host.py",
            "portable/canonical_evidence_ledger.py",
            "tests/test_canonical_evidence_ledger.py",
            "portable/agency_evolution_gates.py",
            "tests/test_agency_evolution_gates.py",
        )
        for path in required_paths:
            with self.subTest(path=path):
                self.assertTrue(
                    any(fnmatchcase(path, pattern) for pattern in patterns),
                    f"{path} must trigger Runtime Resilience Matrix",
                )


    def test_guarded_filesystem_and_network_boundaries_require_focused_gates(self):
        filesystem = classify_path("portable/guarded_filesystem.py")
        self.assertTrue(filesystem.hardening_required)
        self.assertIn("tests.test_guarded_filesystem", filesystem.required_suites)
        network = classify_path("portable/guarded_network.py")
        self.assertTrue(network.hardening_required)
        self.assertIn("tests.test_guarded_network", network.required_suites)
        agency_sync = classify_path("scripts/sync_agency_agents.py")
        self.assertTrue(agency_sync.hardening_required)
        self.assertIn("tests.test_guarded_network", agency_sync.required_suites)
        for path in ("tests/test_guarded_filesystem.py", "tests/test_guarded_network.py"):
            with self.subTest(path=path):
                impact = classify_path(path)
                self.assertTrue(impact.hardening_required)

    def test_unclassified_sensitive_path_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "no impact classification"):
            classify_path("portable/new_recovery_queue.py")
        with self.assertRaisesRegex(ValueError, "no impact classification"):
            classify_path("state/new_recovery_gate.json")
        for path in (
            "portable/new_sealed_oracle.py",
            "portable/new_attestation_registry.py",
            "portable/new_independent_evaluation.py",
            "tests/test_new_sealed_corpus.py",
            "portable/new_execution_boundary.py",
            "tests/test_new_ai_coding_agency_bridge.py",
            "portable/new_agency_team_orchestrator.py",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "no impact classification"):
                classify_path(path)

    def test_pr_integration_guard_requires_its_focused_suite_without_runtime_hardening(self):
        for path in ("scripts/pr_integration_guard.py", "tests/test_pr_integration_guard.py"):
            with self.subTest(path=path):
                impact = classify_path(path)
                self.assertIsNotNone(impact)
                self.assertIn("tests.test_pr_integration_guard", impact.required_suites)
                self.assertFalse(impact.hardening_required)

    def test_documentation_only_change_does_not_trigger_expensive_hardening(self):
        result = classify_changes(["docs/PROVIDER_OPERATION_LIFECYCLE.md", "README.md"])
        self.assertFalse(result["hardening_required"])
        self.assertEqual([], result["required_suites"])
        self.assertIsNone(classify_path("docs/PROVIDER_OPERATION_LIFECYCLE.md"))
        cli = classify_path("auren_cli.py")
        self.assertIsNotNone(cli)
        self.assertTrue(cli.hardening_required)
        self.assertIn("tests.test_continuous_execution_cli", cli.required_suites)

    def test_sensitive_change_requires_hardening_and_named_suites(self):
        result = classify_changes(["portable/provider_operation_lifecycle.py"])
        self.assertTrue(result["hardening_required"])
        self.assertIn("tests.test_provider_operation_lifecycle", result["required_suites"])
        self.assertIn("tests.test_side_effect_ledger", result["required_suites"])

    def test_final_status_distinguishes_all_four_outcomes(self):
        result = final_status(
            required=["core", "hardening"],
            passed=["core"], failed=["hardening"], skipped=["optional", "extra"],
            not_required=["optional"],
        )
        self.assertEqual("passed", result["core"])
        self.assertEqual("failed", result["hardening"])
        self.assertEqual("skipped-not-required", result["optional"])
        self.assertEqual("skipped-not-required", result["extra"])
        self.assertEqual("skipped-but-required", final_status(
            required=["hardening"], passed=[], failed=[], skipped=["hardening"]
        )["hardening"])

    def test_required_suite_without_result_fails_closed(self):
        self.assertEqual("failed", final_status(
            required=["hardening"], passed=[], failed=[], skipped=[]
        )["hardening"])


if __name__ == "__main__":
    unittest.main()
