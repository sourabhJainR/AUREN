"""Read-only, cross-domain lifecycle semantics for AUREN surfaces.

This module deliberately does not own or validate runtime transitions. Each domain
keeps its existing authoritative state machine and persistence boundary. This
projection gives dashboards, SDKs and agents a consistent interpretation without
collapsing domain-specific states or granting resume/dispatch authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PROJECTION_VERSION = "auren-lifecycle-projection-v1"


@dataclass(frozen=True)
class _Semantics:
    category: str
    label: str
    terminal: bool = False
    attention_required: bool = False
    resume_policy: str = "controller_decides"
    next_action: str = "inspect_authoritative_state"


def _s(
    category: str,
    label: str,
    *,
    terminal: bool = False,
    attention: bool = False,
    resume: str = "controller_decides",
    action: str,
) -> _Semantics:
    return _Semantics(category, label, terminal, attention, resume, action)


# These are presentation semantics, not alternate transition graphs. Terminality
# is intentionally domain-specific: for example, a failed plan task may be reset
# to ready, while a failed progress-journal task is terminal.
_DOMAIN_STATES: dict[str, dict[str, _Semantics]] = {
    "session": {
        "running": _s("active", "Running", action="monitor_durable_progress_and_deadline"),
        "waiting": _s("waiting", "Waiting", action="wait_for_dispatch_or_required_input"),
        "complete": _s("succeeded", "Completed", terminal=True, resume="never", action="stop_completed_session"),
        "blocked": _s("blocked", "Blocked", attention=True, action="surface_blocker_and_request_authorized_resolution"),
        "failed": _s("failed", "Failed", terminal=True, attention=True, resume="never", action="inspect_failure_before_new_run"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_cancellation_and_stop"),
        "stale": _s("stale", "Stale", attention=True, action="reconcile_checkpoint_and_lease_before_resume"),
        "unverified": _s("unknown", "Unverified", attention=True, action="inspect_authoritative_checkpoint"),
    },
    "progress_task": {
        "pending": _s("not_started", "Pending", action="check_queue_admission_and_dependencies"),
        "queued": _s("ready", "Queued", action="dispatch_through_canonical_controller"),
        "waiting_approval": _s("waiting", "Awaiting approval", attention=True, action="await_authorized_approval"),
        "waiting_quota": _s("waiting", "Waiting for quota", action="wait_for_quota_window_or_operator_action"),
        "blocked_approval": _s("blocked", "Approval blocked", attention=True, action="request_authorized_approval"),
        "blocked_quota": _s("blocked", "Quota blocked", attention=True, action="wait_for_quota_or_authorized_unblock"),
        "blocked_reconciliation": _s("uncertain", "Reconciliation required", attention=True, resume="reconcile_first", action="reconcile_task_and_side_effect_records_before_retry"),
        "stale": _s("stale", "Stale", attention=True, action="reconcile_checkpoint_and_lease_before_resume"),
        "unknown": _s("unknown", "Unknown", attention=True, action="inspect_authoritative_task_store"),
        "unscored": _s("unknown", "Unscored", attention=True, action="collect_required_verification_evidence"),
        "timed_out": _s("failed", "Timed out", terminal=True, attention=True, resume="never", action="inspect_timeout_and_recovery_evidence"),
        "running": _s("active", "Running", action="monitor_durable_progress_and_deadline"),
        "blocked": _s("blocked", "Blocked", attention=True, action="surface_blocker_and_request_authorized_resolution"),
        "recovering": _s("recovering", "Recovering", attention=True, action="reconcile_checkpoint_and_lease_before_resume"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="stop_completed_task"),
        "failed": _s("failed", "Failed", terminal=True, attention=True, resume="never", action="inspect_failure_before_new_run"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_cancellation_and_stop"),
    },
    "plan_task": {
        "pending": _s("not_started", "Pending", action="check_dependencies_and_wait_for_admission"),
        "ready": _s("ready", "Ready", action="dispatch_through_canonical_controller"),
        "running": _s("active", "Running", action="monitor_durable_progress_and_deadline"),
        "verifying": _s("verifying", "Verifying", action="collect_and_check_acceptance_evidence"),
        "passed": _s("succeeded", "Passed", terminal=True, resume="never", action="stop_verified_task"),
        "blocked": _s("blocked", "Blocked", attention=True, action="resolve_blocker_then_recheck_controller"),
        "failed": _s("failed", "Failed", attention=True, action="inspect_failure_and_require_new_evidence_before_retry"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_cancellation_and_stop"),
    },
    "plan_run": {
        "planned": _s("not_started", "Planned", action="check_dependencies_and_admission"),
        "ready": _s("ready", "Ready", action="dispatch_through_canonical_controller"),
        "running": _s("active", "Running", action="monitor_plan_tasks_and_deadline"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="stop_verified_plan"),
        "failed": _s("failed", "Failed", attention=True, action="inspect_failed_tasks_and_recovery_evidence"),
        "blocked": _s("blocked", "Blocked", attention=True, action="surface_blockers_and_required_authority"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, attention=True, resume="never", action="preserve_cancellation_and_inspect_plan"),
        "unknown": _s("unknown", "Unknown", attention=True, action="reconcile_plan_store_and_checkpoint"),
    },
    "workspace": {
        "creating": _s("active", "Creating", action="monitor_workspace_allocation"),
        "active": _s("active", "Active", action="monitor_workspace_ownership_and_worker"),
        "running": _s("active", "Running", action="monitor_workspace_ownership_and_worker"),
        "proposal_ready": _s("ready", "Proposal ready", action="request_independent_review"),
        "review_pending": _s("waiting", "Review pending", action="wait_for_independent_review"),
        "adopting": _s("recovering", "Adopting", attention=True, action="verify_adoption_receipt_and_recovery_ledger"),
        "adopted": _s("succeeded", "Adopted", terminal=True, resume="never", action="verify_adoption_receipt_and_close_workspace"),
        "merged": _s("succeeded", "Merged", terminal=True, resume="never", action="verify_merge_and_close_workspace"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="close_workspace_after_verification"),
        "cleaned": _s("succeeded", "Cleaned", terminal=True, resume="never", action="preserve_workspace_cleanup_receipt"),
        "unknown": _s("unknown", "Unknown", attention=True, action="reconcile_workspace_registry_and_lease"),
        "stopped": _s("cancelled", "Stopped", terminal=True, resume="never", action="preserve_workspace_stop_receipt"),
        "cancel_requested": _s("waiting", "Cancellation requested", attention=True, action="await_worker_termination_receipt"),
        "failed": _s("failed", "Failed", attention=True, action="inspect_workspace_failure"),
        "creation_failed": _s("failed", "Creation failed", attention=True, action="inspect_workspace_creation_failure"),
        "blocked": _s("blocked", "Blocked", attention=True, action="resolve_workspace_blocker"),
        "stale": _s("stale", "Stale", attention=True, action="reconcile_workspace_owner_and_lease"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_workspace_cancellation"),
        "closed": _s("cancelled", "Closed", terminal=True, resume="never", action="inspect_workspace_close_receipt"),
    },
    "approval": {
        "pending": _s("waiting", "Awaiting approval", attention=True, action="await_authorized_approval"),
        "approved": _s("ready", "Approved", action="resume_only_through_approval_controller"),
        "executing": _s("active", "Executing", action="monitor_approved_operation"),
        "outcome_unknown": _s("uncertain", "Outcome unknown", attention=True, resume="reconcile_first", action="reconcile_external_effect_before_retry"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="close_approval_operation"),
        "failed": _s("failed", "Failed", terminal=True, attention=True, resume="never", action="inspect_approved_operation_failure"),
        "rejected": _s("rejected", "Rejected", terminal=True, attention=True, resume="never", action="stop_rejected_operation"),
        "expired": _s("expired", "Expired", terminal=True, attention=True, resume="never", action="request_new_approval_if_still_needed"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_approval_cancellation"),
        "superseded": _s("rejected", "Superseded", terminal=True, resume="never", action="use_current_approval_record"),
    },
    "side_effect": {
        "executing": _s("active", "Executing", action="observe_operation_without_replaying"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="reuse_verified_receipt"),
        "reconcile_required": _s("uncertain", "Reconciliation required", attention=True, resume="reconcile_first", action="reconcile_external_effect_before_retry"),
        "retry_authorized": _s("ready", "Retry authorized", attention=True, action="execute_once_with_original_idempotency_key"),
    },
    "provider_operation": {
        "dispatching": _s("active", "Dispatching", action="observe_provider_operation"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="reuse_verified_provider_receipt"),
        "reconcile_required": _s("uncertain", "Reconciliation required", attention=True, resume="reconcile_first", action="lookup_and_reconcile_before_retry"),
        "blocked": _s("blocked", "Blocked", attention=True, resume="reconcile_first", action="reconcile_provider_operation_before_retry"),
    },
    "agent_turn": {
        "idle": _s("not_started", "Idle", action="start_only_when_task_is_admitted"),
        "planning": _s("active", "Planning", action="continue_bounded_agent_turn"),
        "acting": _s("active", "Acting", action="observe_tool_and_provider_results"),
        "observing": _s("active", "Observing", action="evaluate_observation_and_evidence"),
        "verifying": _s("verifying", "Verifying", action="check_acceptance_and_evidence"),
        "deciding": _s("verifying", "Deciding", action="choose_continue_repair_stop_or_complete"),
        "repairing": _s("recovering", "Repairing", attention=True, action="run_bounded_repair_with_new_evidence"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="stop_completed_turn"),
        "stopped": _s("cancelled", "Stopped", terminal=True, resume="never", action="preserve_stop_decision"),
        "failed": _s("failed", "Failed", terminal=True, attention=True, resume="never", action="inspect_turn_failure"),
    },
    "agency_execution": {
        "created": _s("not_started", "Created", action="plan_before_execution"),
        "planned": _s("ready", "Planned", action="dispatch_after_guard_checks"),
        "executing": _s("active", "Executing", action="monitor_execution_and_evidence"),
        "verifying": _s("verifying", "Verifying", action="collect_independent_verification"),
        "repairing": _s("recovering", "Repairing", attention=True, action="perform_bounded_repair"),
        "escalated": _s("blocked", "Escalated", attention=True, action="request_authorized_operator_decision"),
        "passed": _s("succeeded", "Passed", terminal=True, resume="never", action="stop_verified_execution"),
        "failed": _s("failed", "Failed", terminal=True, attention=True, resume="never", action="inspect_execution_failure"),
        "blocked": _s("blocked", "Blocked", terminal=True, attention=True, resume="never", action="stop_and_surface_blocker"),
    },
    "learning_hypothesis": {
        "observed": _s("not_started", "Observed", action="collect_supporting_evidence"),
        "investigated": _s("active", "Investigated", action="form_evidence_bound_hypothesis"),
        "hypothesized": _s("active", "Hypothesized", action="define_testable_change_and_acceptance"),
        "planned": _s("ready", "Planned", action="evaluate_against_pinned_baseline"),
        "evaluated": _s("verifying", "Evaluated", action="request_independent_promotion_decision"),
        "approved": _s("ready", "Approved", action="deploy_only_through_guarded_controller"),
        "deployed": _s("active", "Deployed", action="run_canary_and_collect_independent_evidence"),
        "verified": _s("succeeded", "Verified", terminal=True, resume="never", action="retain_verified_learning"),
        "rejected": _s("rejected", "Rejected", terminal=True, resume="never", action="retain_rejection_evidence"),
        "blocked": _s("blocked", "Blocked", attention=True, action="resolve_evidence_or_policy_gap"),
        "inconclusive": _s("uncertain", "Inconclusive", attention=True, action="collect_more_independent_evidence"),
        "rolled_back": _s("recovering", "Rolled back", attention=True, action="verify_rollback_then_reinvestigate"),
    },
    "feedback_loop": {
        "success": _s("succeeded", "Succeeded", terminal=True, resume="never", action="stop_successful_loop"),
        "clean_no_op": _s("succeeded", "No change needed", terminal=True, resume="never", action="record_clean_no_op_and_stop"),
        "blocked": _s("blocked", "Blocked", terminal=True, attention=True, resume="never", action="surface_blocker_and_stop"),
        "approval_required": _s("waiting", "Approval required", terminal=True, attention=True, resume="controller_decides", action="await_approval_before_new_pass"),
        "exhausted": _s("failed", "Budget exhausted", terminal=True, attention=True, resume="never", action="stop_and_request_new_budget"),
        "no_progress": _s("blocked", "No progress", terminal=True, attention=True, resume="never", action="stop_and_replan_with_new_evidence"),
        "error": _s("failed", "Error", terminal=True, attention=True, resume="never", action="inspect_loop_error"),
    },
    "goal": {
        "open": _s("not_started", "Open", action="check_goal_dependencies_and_priority"),
        "active": _s("active", "Active", action="monitor_goal_progress_and_acceptance"),
        "blocked": _s("blocked", "Blocked", attention=True, action="resolve_goal_dependency_or_authority"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="stop_completed_goal"),
        "abandoned": _s("cancelled", "Abandoned", terminal=True, resume="never", action="preserve_abandonment_decision"),
    },

    "capability": {
        "canary": _s("verifying", "Canary", action="collect_independent_canary_evidence"),
        "promotion_blocked": _s("blocked", "Promotion blocked", attention=True, action="resolve_promotion_evidence_gaps"),
        "promoted": _s("succeeded", "Promoted", terminal=True, resume="never", action="retain_verified_capability"),
        "rolled_back": _s("recovering", "Rolled back", attention=True, action="verify_rollback_and_reinvestigate"),
    },
    "invention": {
        "candidate": _s("not_started", "Candidate", action="benchmark_candidate_against_pinned_baseline"),
        "canary": _s("verifying", "Canary", action="collect_independent_invention_canary_evidence"),
        "promotion_blocked": _s("blocked", "Promotion blocked", attention=True, action="resolve_invention_evidence_gap"),
        "promoted": _s("succeeded", "Promoted", terminal=True, resume="never", action="retain_verified_invention"),
        "rolled_back": _s("recovering", "Rolled back", attention=True, action="verify_rollback_then_reinvestigate"),
    },
    "experiment": {
        "queued": _s("ready", "Queued", action="wait_for_experiment_admission"),
        "assigned": _s("active", "Assigned", action="collect_controlled_experiment_observations"),
        "observed": _s("verifying", "Observed", action="check_attribution_and_evidence_quality"),
        "attributed": _s("succeeded", "Attributed", terminal=True, resume="never", action="retain_attribution_receipt"),
    },
    "work_unit": {
        "created": _s("not_started", "Created", action="check_scope_and_dependencies"),
        "open": _s("not_started", "Open", action="check_scope_and_dependencies"),
        "running": _s("active", "Running", action="monitor_work_unit_and_evidence"),
        "waiting_ci": _s("waiting", "Waiting for CI", action="poll_exact_head_required_checks"),
        "waiting_external": _s("waiting", "Waiting for input", action="await_required_external_input"),
        "resolved": _s("verifying", "Resolved", action="verify_work_unit_acceptance_evidence"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="stop_completed_work_unit"),
        "failed": _s("failed", "Failed", terminal=True, attention=True, resume="never", action="inspect_failure_and_recovery_receipts"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_cancellation_and_stop"),
    },
    "evidence_outbox": {
        "pending": _s("ready", "Pending delivery", action="deliver_evidence_with_stable_event_id"),
        "sending": _s("active", "Sending", action="observe_claim_and_delivery_receipt"),
        "sent": _s("succeeded", "Sent", terminal=True, resume="never", action="retain_sink_receipt"),
    },
    "graph_worker": {
        "pending": _s("ready", "Pending", action="verify_dependencies_and_claim_worker"),
        "queued": _s("ready", "Queued", action="verify_dependencies_and_claim_worker"),
        "waiting": _s("waiting", "Waiting", action="inspect_dependency_or_admission_reason"),
        "working": _s("active", "Working", action="monitor_worker_lease_and_proposal"),
        "running": _s("active", "Running", action="monitor_worker_lease_and_proposal"),
        "adopting": _s("recovering", "Adopting", attention=True, action="verify_adoption_receipt_and_recovery_ledger"),
        "complete": _s("succeeded", "Complete", terminal=True, resume="never", action="verify_worker_completion_receipt"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="verify_worker_completion_receipt"),
        "done": _s("succeeded", "Done", terminal=True, resume="never", action="verify_worker_completion_receipt"),
        "failed": _s("failed", "Failed", attention=True, action="inspect_worker_failure_and_recovery"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_worker_cancellation"),
        "blocked": _s("blocked", "Blocked", attention=True, action="reconcile_worker_and_progress_state"),
        "unknown": _s("unknown", "Unknown", attention=True, action="reconcile_worker_ledger_and_progress_journal"),
    },
    "continuous_execution": {
        "running": _s("active", "Running", action="monitor_supervisor_cycle_and_deadline"),
        "stop_requested": _s("waiting", "Stop requested", attention=True, action="wait_for_supervisor_to_acknowledge_stop"),
        "stopped": _s("cancelled", "Stopped", terminal=True, resume="never", action="preserve_durable_stop"),
        "failed": _s("failed", "Failed", terminal=True, attention=True, resume="never", action="inspect_supervisor_failure_and_control_lease"),
    },
    "release_action": {
        "shadow": _s("verifying", "Shadow channel set", action="collect_shadow_verification_evidence"),
        "canary": _s("verifying", "Canary channel set", action="collect_canary_and_regression_evidence"),
        "promote": _s("succeeded", "Promoted", terminal=True, resume="never", action="verify_current_channel_and_retain_receipt"),
        "rollback": _s("recovering", "Rolled back", attention=True, action="verify_previous_artifact_and_runtime_health"),
    },

    "engineering_gate": {
        "pending": _s("not_started", "Pending", action="wait_for_gate_dependencies"),
        "ready": _s("ready", "Ready", action="dispatch_required_gate_crews"),
        "in_progress": _s("active", "In progress", action="collect_gate_exit_evidence"),
        "passed": _s("succeeded", "Passed", terminal=True, resume="never", action="retain_gate_evidence"),
        "blocked": _s("blocked", "Blocked", attention=True, action="resolve_gate_dependency_or_external_evidence"),
        "failed": _s("failed", "Failed", attention=True, action="inspect_gate_failure_before_replan"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_gate_cancellation"),
    },
    "worktree": {
        "creating": _s("active", "Creating", action="monitor_worktree_creation"),
        "active": _s("active", "Active", action="verify_task_intent_budget_and_owned_path_binding"),
        "creation_failed": _s("failed", "Creation failed", attention=True, action="inspect_worktree_creation_failure"),
        "cleaned": _s("cancelled", "Cleaned", terminal=True, resume="never", action="preserve_worktree_cleanup_receipt"),
    },
    "remediation": {
        "pending": _s("not_started", "Pending", action="prioritize_remediation_with_evidence"),
        "in_progress": _s("active", "In progress", action="monitor_remediation_and_acceptance"),
        "blocked": _s("blocked", "Blocked", attention=True, action="resolve_remediation_blocker"),
        "resolved": _s("verifying", "Resolved", action="verify_resolution_evidence"),
        "accepted": _s("succeeded", "Accepted", terminal=True, resume="never", action="retain_accepted_resolution"),
        "deferred": _s("waiting", "Deferred", action="retain_deferral_reason_and_revisit_condition"),
    },
    "goal_planning": {
        "open": _s("not_started", "Open", action="check_goal_dependencies_and_priority"),
        "active": _s("active", "Active", action="monitor_goal_progress"),
        "blocked": _s("blocked", "Blocked", attention=True, action="resolve_goal_dependency"),
        "complete": _s("succeeded", "Complete", terminal=True, resume="never", action="retain_goal_completion_evidence"),
        "abandoned": _s("cancelled", "Abandoned", terminal=True, resume="never", action="preserve_goal_abandonment"),
    },
    "transfer_learning": {
        "promoted": _s("succeeded", "Promoted", terminal=True, resume="never", action="retain_independent_transfer_promotion_receipt"),
        "rolled_back": _s("recovering", "Rolled back", attention=True, action="verify_prior_version_and_retest"),
    },

    "crew_handoff": {
        "queued": _s("ready", "Queued", action="claim_only_from_active_graph_node"),
        "claimed": _s("active", "Claimed", action="run_target_crew_and_request_independent_review"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="retain_independent_review_receipt"),
        "rejected": _s("rejected", "Rejected", terminal=True, resume="never", action="retain_rejection_receipt_without_redispatch"),
    },
    "cognitive_phase": {
        "observe": _s("active", "Observing", action="record_bounded_observations"),
        "evaluate": _s("verifying", "Evaluating", action="evaluate_observations_against_acceptance"),
        "learn": _s("active", "Learning", action="record_only_evidence_supported_learning"),
        "complete": _s("succeeded", "Complete", terminal=True, resume="never", action="retain_episode_receipt"),
    },

    "controller_task": {
        "pending": _s("not_started", "Pending", action="check_admission_and_dependencies"),
        "queued": _s("ready", "Queued", action="dispatch_through_execution_controller"),
        "running": _s("active", "Running", action="monitor_controller_lease_and_progress"),
        "waiting_approval": _s("waiting", "Awaiting approval", attention=True, action="await_authorized_approval"),
        "blocked_approval": _s("blocked", "Approval blocked", attention=True, action="request_authorized_approval"),
        "waiting_quota": _s("waiting", "Waiting for quota", action="wait_for_retry_deadline_or_operator_action"),
        "blocked_quota": _s("blocked", "Quota blocked", attention=True, action="wait_for_quota_or_authorized_unblock"),
        "blocked_reconciliation": _s("uncertain", "Reconciliation required", attention=True, resume="reconcile_first", action="reconcile_provider_effect_before_resume"),
        "blocked": _s("blocked", "Blocked", attention=True, action="inspect_controller_blocker_and_recovery_record"),
        "recovering": _s("recovering", "Recovering", attention=True, action="reconcile_checkpoint_and_lease_before_resume"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="stop_completed_task"),
        "failed": _s("failed", "Failed", terminal=True, attention=True, resume="never", action="inspect_failure_before_new_run"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_cancellation_and_stop"),
    },
    "trigger_task": {
        "pending": _s("ready", "Queued", action="wait_for_trigger_admission"),
        "claimed": _s("active", "Claimed", action="monitor_trigger_lease_and_runtime"),
        "dispatched": _s("active", "Dispatched", action="observe_runtime_task_and_completion_receipt"),
        "success": _s("succeeded", "Succeeded", terminal=True, resume="never", action="retain_trigger_completion_receipt"),
        "complete": _s("succeeded", "Complete", terminal=True, resume="never", action="retain_trigger_completion_receipt"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="retain_trigger_completion_receipt"),
        "failed": _s("failed", "Failed", terminal=True, attention=True, resume="never", action="inspect_trigger_failure_before_new_submission"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_trigger_cancellation"),
    },

    "workspace_adoption": {
        "applying": _s("active", "Applying", action="verify_exact_base_and_combined_patch"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="verify_adoption_receipt_and_commit"),
        "failed": _s("failed", "Failed", terminal=True, attention=True, resume="never", action="verify_rollback_before_new_adoption"),
    },
    "ci_poll": {
        "pending": _s("not_started", "Pending", action="start_exact_head_required_check_poll"),
        "running": _s("active", "Running", action="poll_required_checks_for_exact_head"),
        "waiting_for_required_checks": _s("waiting", "Waiting for required checks", action="continue_bounded_exact_head_polling"),
        "waiting_for_main_head": _s("waiting", "Waiting for main", action="verify_main_head_and_post_merge_checks"),
        "observed_complete_unverified": _s("verifying", "Observed complete, unverified", attention=True, action="obtain_exact_head_completion_evidence"),
        "success": _s("succeeded", "Checks passed", terminal=True, resume="never", action="verify_exact_head_and_required_check_set"),
        "failure": _s("failed", "Checks failed", attention=True, action="inspect_failed_check_and_bounded_repair_policy"),
        "poll_error": _s("uncertain", "Polling error", attention=True, action="retry_bounded_poll_without_claiming_completion"),
    },
    "ci_repair": {
        "observed": _s("not_started", "Observed", action="inspect_check_evidence_before_triage"),
        "triaged": _s("ready", "Triaged", action="decide_whether_bounded_repair_is_authorized"),
        "queued": _s("ready", "Queued", action="wait_for_repair_task_admission"),
        "claimed": _s("active", "Claimed", action="verify_repair_task_lease"),
        "repairing": _s("active", "Repairing", action="monitor_bounded_repair_and_new_evidence"),
        "validating": _s("verifying", "Validating", action="verify_exact_head_and_required_checks"),
        "resolved": _s("succeeded", "Resolved", terminal=True, resume="never", action="retain_passing_repair_validation_receipt"),
        "blocked": _s("blocked", "Blocked", terminal=True, attention=True, resume="never", action="surface_repair_blocker_and_stop"),
    },
    "goal_reconciliation": {
        "reconciled": _s("succeeded", "Reconciled", terminal=True, resume="never", action="retain_goal_reconciliation_receipt"),
        "blocked": _s("blocked", "Blocked", attention=True, action="resolve_goal_reconciliation_blocker"),
        "waiting_for_approval": _s("waiting", "Waiting for approval", attention=True, action="await_authorized_approval"),
        "waiting_external_dependency": _s("waiting", "Waiting for dependency", action="await_external_dependency_evidence"),
        "paused": _s("waiting", "Paused", attention=True, action="await_authorized_resume"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_goal_cancellation"),
        "cancel_requested": _s("waiting", "Cancellation requested", attention=True, action="await_supervisor_stop_receipt"),
        "user_cancelled": _s("cancelled", "Cancelled by user", terminal=True, resume="never", action="preserve_user_cancellation"),
        "retry_budget_exhausted": _s("blocked", "Retry budget exhausted", terminal=True, attention=True, resume="never", action="stop_until_new_evidence_and_authorized_wake"),
    },
    "team_member": {
        "pending": _s("not_started", "Pending", action="wait_for_verified_assignment"),
        "assigned": _s("ready", "Assigned", action="verify_assignment_and_workspace_receipts_before_start"),
        "running": _s("active", "Running", action="monitor_member_lease_and_output"),
        "blocked": _s("blocked", "Blocked", attention=True, action="inspect_member_failure_and_manager_handoff"),
        "completed": _s("succeeded", "Completed", terminal=True, resume="never", action="verify_member_execution_receipt"),
        "failed": _s("failed", "Failed", terminal=True, attention=True, resume="never", action="inspect_member_failure_receipt"),
    },
    "team_budget": {
        "reserved": _s("active", "Reserved", action="monitor_member_budget_and_execution"),
        "settled": _s("succeeded", "Settled", terminal=True, resume="never", action="verify_budget_settlement_receipt"),
        "released": _s("cancelled", "Released", terminal=True, resume="never", action="retain_budget_release_receipt"),
        "overrun": _s("blocked", "Overrun", terminal=True, attention=True, resume="never", action="stop_member_and_reconcile_budget"),
    },
    "admission_control": {
        "active": _s("ready", "Admissions active", action="admit_only_after_policy_and_capacity_checks"),
        "paused": _s("blocked", "Admissions paused", attention=True, action="await_authorized_admission_resume"),
        "audit_invalid": _s("blocked", "Audit invalid", terminal=True, attention=True, resume="never", action="fail_closed_and_reconcile_control_audit"),
    },
    "task_memory_promotion": {
        "candidate": _s("not_started", "Candidate", action="collect_repeated_observations_and_supporting_evidence"),
        "verified": _s("succeeded", "Verified", terminal=True, resume="never", action="retain_verified_immutable_learning_revision"),
        "superseded": _s("rejected", "Superseded", terminal=True, resume="never", action="follow_superseding_learning_revision"),
        "rejected": _s("rejected", "Rejected", terminal=True, resume="never", action="retain_rejection_reason_and_evidence"),
    },
    "legacy_task": {
        "pending": _s("not_started", "Pending", action="check_dependencies_before_dispatch"),
        "in-progress": _s("active", "In progress", action="monitor_task_progress"),
        "blocked": _s("blocked", "Blocked", attention=True, action="surface_task_blocker"),
        "done": _s("succeeded", "Done", terminal=True, resume="never", action="stop_completed_task"),
        "cancelled": _s("cancelled", "Cancelled", terminal=True, resume="never", action="preserve_task_cancellation"),
    },
}


_UNKNOWN = _s(
    "unknown",
    "Unknown",
    attention=True,
    action="inspect_authoritative_store_and_fail_closed",
)


def project_lifecycle(domain: str, state: Any) -> dict[str, Any]:
    """Return a stable, read-only lifecycle envelope for a domain state.

    Unknown domains and states are surfaced as unknown; they are never inferred
    to be healthy, complete, or safe to resume.
    """
    domain_name = str(domain or "").strip().lower()
    source_state = str(state or "").strip().lower()
    semantics = _DOMAIN_STATES.get(domain_name, {}).get(source_state, _UNKNOWN)
    return {
        "schema_version": PROJECTION_VERSION,
        "domain": domain_name or "unknown",
        "source_state": source_state or "unknown",
        "category": semantics.category,
        "label": semantics.label,
        "terminal": semantics.terminal,
        "attention_required": semantics.attention_required,
        "resume_policy": semantics.resume_policy,
        "next_action": semantics.next_action,
    }


def lifecycle_domains() -> tuple[str, ...]:
    """Return the known domain names for documentation and contract tests."""
    return tuple(sorted(_DOMAIN_STATES))
