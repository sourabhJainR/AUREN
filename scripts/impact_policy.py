"""Testable impact policy for recovery and security-sensitive source changes.

This policy is intentionally narrow: ordinary documentation changes do not trigger
expensive hardening. New/changed sensitive paths without a classification fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath


@dataclass(frozen=True)
class Impact:
    required_suites: tuple[str, ...]
    hardening_required: bool


# Order is stable for deterministic evidence. Every sensitive source path has
# a mandatory focused suite; recovery changes also require production hardening.
_RULES: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("portable/agency_observability_ops.py", ("tests.test_agency_observability_ops", "tests.test_agency_observability"), True),
    ("scripts/pr_integration_guard.py", ("tests.test_pr_integration_guard",), True),
    ("tests/test_pr_integration_guard.py", ("tests.test_pr_integration_guard",), True),
    ("tests/test_agency_observability_ops.py", ("tests.test_agency_observability_ops",), True),
    ("portable/agency_release_lifecycle.py", ("tests.test_agency_release_lifecycle", "tests.test_agency_observability"), True),
    ("tests/test_agency_release_lifecycle.py", ("tests.test_agency_release_lifecycle",), True),
    ("portable/agency_runtime.py", ("tests.test_agency_runtime", "tests.test_agency_runtime_release_integration", "tests.test_agency_release_lifecycle"), True),
    ("tests/test_agency_runtime.py", ("tests.test_agency_runtime",), True),
    ("tests/test_agency_runtime_release_integration.py", ("tests.test_agency_runtime_release_integration", "tests.test_agency_release_lifecycle"), True),
    ("portable/agency_observability.py", ("tests.test_agency_observability",), True),
    ("tests/test_agency_observability.py", ("tests.test_agency_observability",), True),
    ("portable/engineering_dashboard.py", ("tests.test_engineering_dashboard",), True),
    ("tests/test_engineering_dashboard.py", ("tests.test_engineering_dashboard",), True),
    (".ai-harness/runtime/*evaluator*.py", ("tests.test_canary_evaluator", "tests.test_production_evaluator_trust"), True),
    ("portable/external_evaluation_campaign.py", ("tests.test_external_evaluation_campaign", "tests.test_external_evaluator_gateway", "tests.test_sealed_arena_boundary"), True),
    ("portable/independent_evaluation_attestation.py", ("tests.test_independent_evaluation_attestation", "tests.test_sealed_arena_boundary", "tests.test_sealed_arena_signature"), True),
    ("portable/attestation_freshness.py", ("tests.test_attestation_freshness", "tests.test_postgres_attestation_replay_registry"), True),
    ("portable/postgres_attestation_replay_registry.py", ("tests.test_postgres_attestation_replay_registry", "tests.test_attestation_freshness"), True),
    ("tests/test_attestation_freshness.py", ("tests.test_attestation_freshness", "tests.test_postgres_attestation_replay_registry"), True),
    ("tests/test_postgres_attestation_replay_registry.py", ("tests.test_postgres_attestation_replay_registry", "tests.test_attestation_freshness"), True),
    ("tests/test_canary_evaluator.py", ("tests.test_canary_evaluator",), True),
    ("tests/test_production_evaluator_trust.py", ("tests.test_production_evaluator_trust",), True),
    ("tests/test_external_evaluation_campaign.py", ("tests.test_external_evaluation_campaign",), True),
    ("tests/test_independent_evaluation_attestation.py", ("tests.test_independent_evaluation_attestation",), True),
    ("portable/*arena*.py", ("tests.test_arena_acceptance", "tests.test_arena_campaign_packet", "tests.test_arena_run_receipt", "tests.test_arena_trust", "tests.test_container_sandboxed_arena", "tests.test_container_sandboxed_arena_integration", "tests.test_external_evaluator_gateway", "tests.test_external_evaluator_gateway_budget", "tests.test_generalization_arena", "tests.test_independent_arena", "tests.test_isolated_arena_runner", "tests.test_sealed_arena_boundary", "tests.test_sealed_arena_custody", "tests.test_sealed_arena_exchange", "tests.test_sealed_arena_policy", "tests.test_sealed_arena_replay_concurrency", "tests.test_sealed_arena_signature", "tests.test_sealed_corpus_provenance", "tests.test_sealed_cross_domain_arena"), True),
    ("portable/*evaluator*.py", ("tests.test_external_evaluator_gateway", "tests.test_external_evaluator_gateway_budget", "tests.test_sealed_arena_boundary", "tests.test_sealed_arena_signature", "tests.test_sealed_arena_custody"), True),
    ("portable/*sandbox*.py", ("tests.test_container_sandboxed_arena", "tests.test_container_sandboxed_arena_integration", "tests.test_isolated_arena_runner"), True),
    ("portable/sealed_*.py", ("tests.test_sealed_arena_boundary", "tests.test_sealed_arena_custody", "tests.test_sealed_arena_exchange", "tests.test_sealed_arena_policy", "tests.test_sealed_arena_replay_concurrency", "tests.test_sealed_arena_signature", "tests.test_sealed_corpus_provenance", "tests.test_sealed_cross_domain_arena"), True),
    ("tests/test_*arena*.py", ("tests.test_arena_acceptance", "tests.test_arena_campaign_packet", "tests.test_arena_run_receipt", "tests.test_arena_trust", "tests.test_container_sandboxed_arena", "tests.test_container_sandboxed_arena_integration", "tests.test_external_evaluator_gateway", "tests.test_external_evaluator_gateway_budget", "tests.test_generalization_arena", "tests.test_independent_arena", "tests.test_isolated_arena_runner", "tests.test_sealed_arena_boundary", "tests.test_sealed_arena_custody", "tests.test_sealed_arena_exchange", "tests.test_sealed_arena_policy", "tests.test_sealed_arena_replay_concurrency", "tests.test_sealed_arena_signature", "tests.test_sealed_corpus_provenance", "tests.test_sealed_cross_domain_arena"), True),
    ("tests/test_*evaluator*.py", ("tests.test_external_evaluator_gateway", "tests.test_external_evaluator_gateway_budget", "tests.test_sealed_arena_boundary", "tests.test_sealed_arena_signature", "tests.test_sealed_arena_custody"), True),
    ("tests/test_sealed_corpus*.py", ("tests.test_sealed_corpus_provenance", "tests.test_sealed_arena_boundary", "tests.test_sealed_arena_custody"), True),
    ("portable/side_effect_ledger.py", ("tests.test_side_effect_ledger", "tests.test_provider_operation_lifecycle"), True),
    ("portable/durable_approval.py", ("tests.test_durable_approval", "tests.test_side_effect_ledger", "tests.test_execution_controller", "tests.test_execution_boundary"), True),
    ("portable/provider_operation_lifecycle.py", ("tests.test_provider_operation_lifecycle", "tests.test_side_effect_ledger"), True),
    ("portable/ci_repair_watchdog.py", ("tests.test_ci_repair_watchdog", "tests.test_engineering_recovery"), True),
    ("portable/action_governance.py", ("tests.test_durable_approval", "tests.test_execution_boundary"), True),
    ("portable/execution_boundary.py", ("tests.test_execution_boundary", "tests.test_durable_approval"), True),
    ("portable/continuous_execution.py", ("tests.test_continuous_execution", "tests.test_ci_repair_watchdog", "tests.test_engineering_recovery"), True),
    ("tests/test_continuous_execution.py", ("tests.test_continuous_execution",), True),
    ("tests/test_ci_repair_watchdog.py", ("tests.test_ci_repair_watchdog",), True),
    ("portable/workflow_profile.py", ("tests.test_workflow_profile", "tests.test_workflow_profile_store", "tests.test_workflow_profile_integration"), True),
    ("tests/test_workflow_profile.py", ("tests.test_workflow_profile",), True),
    ("portable/workflow_profile_store.py", ("tests.test_workflow_profile_store", "tests.test_workflow_profile_integration"), True),
    ("tests/test_workflow_profile_integration.py", ("tests.test_workflow_profile_integration",), True),
    ("portable/execution_controller.py", ("tests.test_execution_controller", "tests.test_execution_controller_restart", "tests.test_execution_controller_cancellation", "tests.test_execution_controller_promotion_rollback_contract", "tests.test_provider_operation_lifecycle", "tests.test_durable_approval"), True),
    ("portable/state_graph.py", ("tests.test_runtime_recovery", "tests.test_graph_agent_team", "tests.test_graph_agent_crews"), True),
    ("tests/test_state_graph_observability_live.py", ("tests.test_state_graph_observability_live",), True),
    ("portable/runtime_recovery.py", ("tests.test_runtime_recovery", "tests.test_adaptive_runtime_recovery_integration"), True),
    (".ai-harness/runtime/task_memory.py", ("tests.test_graph_agent_team",), True),
    (".ai-harness/tests/test_task_memory.py", ("tests.test_task_memory", "tests.test_graph_agent_team"), True),
    ("tests/test_runtime_recovery.py", ("tests.test_runtime_recovery",), True),
    ("tests/test_graph_agent_team.py", ("tests.test_graph_agent_team",), True),
    ("portable/agency_state_graph.py", ("tests.test_agency_state_graph", "tests.test_state_graph_hardening"), True),
    ("tests/test_agency_state_graph.py", ("tests.test_agency_state_graph",), True),
    ("portable/hybrid_model_router.py", ("tests.test_hybrid_model_router",), True),
    ("tests/test_hybrid_model_router.py", ("tests.test_hybrid_model_router",), True),
    ("portable/engineering_recovery.py", ("tests.test_engineering_recovery", "tests.test_engineering_recovery_fault_matrix"), True),
    ("portable/engineering_plan_recovery.py", ("tests.test_engineering_plan_recovery",), True),
    ("portable/progress_journal.py", ("tests.test_progress_journal", "tests.test_progress_journal_integration"), True),
    ("portable/canonical_evidence_ledger.py", ("tests.test_canonical_evidence_ledger", "tests.test_provider_operation_lifecycle"), True),
    ("portable/agency_evolution_gates.py", ("tests.test_agency_evolution_gates", "tests.test_promotion_evidence_chain", "tests.test_evidence_freshness"), True),
    ("tests/test_agency_evolution_gates.py", ("tests.test_agency_evolution_gates",), True),
    ("portable/agency_evolution_runtime.py", ("tests.test_agency_evolution_runtime", "tests.test_agency_evolution_gates", "tests.test_execution_controller_restart", "tests.test_provider_operation_lifecycle"), True),
    ("tests/test_agency_evolution_runtime.py", ("tests.test_agency_evolution_runtime",), True),
    ("portable/vibe_coding_adapters.py", ("tests.test_vibe_coding_adapters", "tests.test_provider_operation_lifecycle", "tests.test_side_effect_ledger"), True),
    ("portable/vibe_coding_host.py", ("tests.test_vibe_coding_host", "tests.test_vibe_coding_adapters"), True),
    ("tests/test_vibe_coding_host.py", ("tests.test_vibe_coding_host",), True),
    ("tests/test_vibe_coding_adapters.py", ("tests.test_vibe_coding_adapters",), True),
    ("portable/engineering_evidence_capture.py", ("tests.test_engineering_evidence_capture", "tests.test_execution_controller_evidence_capture"), True),
    ("portable/agency_observability.py", ("tests.test_agency_observability",), True),
    ("tests/test_agency_observability.py", ("tests.test_agency_observability",), True),
    ("portable/engineering_dashboard.py", ("tests.test_engineering_dashboard",), True),
    ("tests/test_engineering_dashboard.py", ("tests.test_engineering_dashboard",), True),
    ("tests/test_engineering_evidence_capture.py", ("tests.test_engineering_evidence_capture",), True),
    ("tests/test_execution_controller_evidence_capture.py", ("tests.test_execution_controller_evidence_capture",), True),
    ("portable/authority/**", ("tests.test_security_gate",), True),
    ("portable/*authority*", ("tests.test_security_gate",), True),
    ("portable/*security*", ("tests.test_security_gate",), True),
    ("state/*authority*", ("tests.test_security_gate",), True),
    ("state/*security*", ("tests.test_security_gate",), True),
    (".ai-harness/authority*", ("tests.test_security_gate",), True),
    ("portable/*rollback*", ("tests.test_state_root_rollback_rehearsal", "tests.test_execution_controller_promotion_rollback_contract", "tests.test_execution_controls"), True),
    ("portable/subprocess_provider.py", ("tests.test_subprocess_provider", "tests.test_bounded_subprocess_fault_injection"), True),
    ("tests/test_subprocess_provider.py", ("tests.test_subprocess_provider",), True),
    ("portable/*provider*", ("tests.test_provider_operation_lifecycle", "tests.test_side_effect_ledger"), True),
    ("tests/test_execution_controller*.py", ("tests.test_execution_controller", "tests.test_execution_controller_restart", "tests.test_execution_controller_cancellation", "tests.test_execution_controller_promotion_rollback_contract"), True),
    ("tests/test_consequential_run_once_recovery.py", ("tests.test_consequential_run_once_recovery", "tests.test_execution_controller"), True),
    ("tests/test_consequential_outer_receipt_crash.py", ("tests.test_consequential_outer_receipt_crash", "tests.test_consequential_run_once_recovery", "tests.test_execution_controller"), True),
    ("tests/test_progress_journal*.py", ("tests.test_progress_journal", "tests.test_progress_journal_integration"), True),
    ("tests/test_side_effect_ledger*.py", ("tests.test_side_effect_ledger", "tests.test_side_effect_ledger_crash_fault_injection"), True),
    ("tests/test_engineering_recovery*.py", ("tests.test_engineering_recovery", "tests.test_engineering_recovery_fault_matrix"), True),
    ("tests/test_engineering_plan_recovery.py", ("tests.test_engineering_plan_recovery",), True),
    ("tests/test_canonical_evidence_ledger.py", ("tests.test_canonical_evidence_ledger",), True),
    ("tests/test_security_gate.py", ("tests.test_security_gate",), True),
    ("tests/test_execution_controls.py", ("tests.test_execution_controls",), True),
    ("tests/test_state_root_rollback_rehearsal.py", ("tests.test_state_root_rollback_rehearsal",), True),
    ("tests/test_provider_operation_lifecycle.py", ("tests.test_provider_operation_lifecycle",), True),
    ("tests/test_side_effect_ledger.py", ("tests.test_side_effect_ledger",), True),
    ("tests/test_durable_approval.py", ("tests.test_durable_approval", "tests.test_side_effect_ledger_crash_fault_injection"), True),
    ("tests/test_continuous_execution_cli.py", ("tests.test_continuous_execution_cli",), True),
    ("scripts/impact_policy.py", ("tests.test_impact_policy",), True),
    ("auren_cli.py", ("tests.test_continuous_execution_cli", "tests.test_continuous_execution"), True),
    ("tests/test_impact_policy.py", ("tests.test_impact_policy",), True),
    (".ai-harness/security*", ("tests.test_security_gate",), True),
    (".ai-harness/runtime/rollback*", ("tests.test_execution_controls", "tests.test_state_root_rollback_rehearsal"), True),
    ("state/*rollback*", ("tests.test_state_root_rollback_rehearsal", "tests.test_execution_controls"), True),
    (".github/workflows/**", ("tests.test_impact_policy",), True),
)

_SENSITIVE_PREFIXES = (
    ".github/workflows/", ".github/actions/",
    ".ai-harness/security", ".ai-harness/runtime/rollback",
)
_SENSITIVE_NAMES = ("authority", "security", "guard", "approval", "rollback", "recovery", "side_effect", "execution_controller",
                    "progress_journal", "canonical_evidence", "engineering_evidence", "provider_operation", "action_governance", "arena", "sealed", "evaluator", "sandbox", "corpus", "external_evaluation", "independent_evaluation", "attestation", "evolution_gates", "improvement_journal")


def is_sensitive_path(path: str) -> bool:
    normalized = PurePosixPath(path).as_posix()
    name = normalized.rsplit("/", 1)[-1].lower()
    # Name-based fail-closed matching applies to executable/configuration/test
    # surfaces, not documentation titles such as PROVIDER_OPERATION_LIFECYCLE.md.
    # Workflow and harness control paths are sensitive regardless of basename.
    if normalized.startswith(_SENSITIVE_PREFIXES):
        return True
    source_or_control = normalized.startswith((
        "portable/", "state/", "scripts/", "tests/",
        ".ai-harness/", ".github/workflows/", ".github/actions/",
    ))
    return source_or_control and any(token in name for token in _SENSITIVE_NAMES)


def classify_path(path: str) -> Impact | None:
    normalized = PurePosixPath(path).as_posix()
    matches = [(suites, hardening) for pattern, suites, hardening in _RULES
               if fnmatchcase(normalized, pattern)]
    if not matches:
        if is_sensitive_path(normalized):
            raise ValueError(f"security-sensitive path has no impact classification: {normalized}")
        return None
    suites = tuple(dict.fromkeys(suite for group, _ in matches for suite in group))
    return Impact(suites, any(hardening for _, hardening in matches))


def classify_changes(paths: list[str]) -> dict[str, object]:
    suites: list[str] = []
    hardening = False
    classified: dict[str, list[str]] = {}
    for path in sorted(set(paths)):
        impact = classify_path(path)
        if impact is None:
            continue
        classified[path] = list(impact.required_suites)
        suites.extend(impact.required_suites)
        hardening = hardening or impact.hardening_required
    return {
        "required_suites": list(dict.fromkeys(suites)),
        "hardening_required": hardening,
        "path_suites": classified,
    }


def final_status(required: list[str], passed: list[str], failed: list[str],
                 skipped: list[str], not_required: list[str] | None = None) -> dict[str, str]:
    """Classify every known suite without treating a required skip as success."""
    required_set, passed_set = set(required), set(passed)
    failed_set, skipped_set = set(failed), set(skipped)
    not_required_set = set(not_required or [])
    result: dict[str, str] = {}
    for suite in sorted(required_set | passed_set | failed_set | skipped_set | not_required_set):
        if suite in failed_set:
            result[suite] = "failed"
        elif suite in skipped_set and suite in required_set:
            result[suite] = "skipped-but-required"
        elif suite in skipped_set or suite in not_required_set or suite not in required_set:
            result[suite] = "skipped-not-required"
        elif suite in passed_set:
            result[suite] = "passed"
        else:
            result[suite] = "failed"
    return result
