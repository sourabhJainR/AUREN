from portable.lifecycle_projection import PROJECTION_VERSION, lifecycle_domains, project_lifecycle


def test_known_domains_have_stable_projection_and_unknown_states_fail_closed():
    assert {
        "session", "progress_task", "plan_task", "plan_run", "workspace",
        "approval", "side_effect", "provider_operation", "agent_turn",
        "agency_execution", "learning_hypothesis", "feedback_loop", "goal",
        "legacy_task", "capability", "invention", "experiment", "work_unit",
        "evidence_outbox", "graph_worker", "continuous_execution", "release",
        "engineering_gate", "worktree", "remediation", "goal_planning",
        "transfer_learning", "crew_handoff", "cognitive_phase",
        "controller_task", "trigger_task",
    } <= set(lifecycle_domains())

    unknown = project_lifecycle("session", "future-state")
    assert unknown == {
        "schema_version": PROJECTION_VERSION,
        "domain": "session",
        "source_state": "future-state",
        "category": "unknown",
        "label": "Unknown",
        "terminal": False,
        "attention_required": True,
        "resume_policy": "controller_decides",
        "next_action": "inspect_authoritative_store_and_fail_closed",
    }


def test_terminality_is_domain_specific_and_does_not_flatten_recoverable_states():
    progress_blocked = project_lifecycle("progress_task", "blocked")
    plan_failed = project_lifecycle("plan_task", "failed")
    approval_failed = project_lifecycle("approval", "failed")
    provider_unknown = project_lifecycle("provider_operation", "reconcile_required")

    assert progress_blocked["category"] == "blocked"
    assert progress_blocked["terminal"] is False
    assert plan_failed["category"] == "failed"
    assert plan_failed["terminal"] is False
    assert approval_failed["terminal"] is True
    assert provider_unknown["category"] == "uncertain"
    assert provider_unknown["resume_policy"] == "reconcile_first"
    assert provider_unknown["next_action"] == "lookup_and_reconcile_before_retry"


def test_success_and_failure_are_never_inferred_from_missing_or_unknown_states():
    for domain in lifecycle_domains():
        projected = project_lifecycle(domain, None)
        assert projected["category"] == "unknown"
        assert projected["terminal"] is False
        assert projected["attention_required"] is True
        assert projected["resume_policy"] == "controller_decides"



def test_workspace_adoption_and_shutdown_states_preserve_terminality():
    adopting = project_lifecycle("workspace", "adopting")
    adopted = project_lifecycle("workspace", "adopted")
    cleaned = project_lifecycle("workspace", "cleaned")
    stopped = project_lifecycle("workspace", "stopped")
    cancel_requested = project_lifecycle("workspace", "cancel_requested")

    assert adopting["category"] == "recovering"
    assert adopting["terminal"] is False
    assert adopted["category"] == "succeeded"
    assert adopted["terminal"] is True
    assert cleaned["label"] == "Cleaned"
    assert cleaned["terminal"] is True
    assert stopped["category"] == "cancelled"
    assert stopped["terminal"] is True
    assert cancel_requested["category"] == "waiting"
    assert cancel_requested["terminal"] is False
    assert cancel_requested["attention_required"] is True


def test_projection_catalog_covers_authoritative_runtime_vocabularies():
    from portable.agency_evolution_gates import HYPOTHESIS_STATES
    from portable.agency_state import ExecutionState
    from portable.durable_approval import STATES as APPROVAL_STATES
    from portable.engineering_plan import TASK_STATUSES
    from portable.feedback_loop import LOOP_TERMINAL_STATES
    from portable.progress_journal import STATES as PROGRESS_STATES
    from portable.engineering_gate_crew_plan import _STATUSES as ENGINEERING_GATE_STATUSES
    from portable.persistent_remediation_backlog import STATUSES as REMEDIATION_STATUSES

    vocabularies = (
        ("workspace", ("running", "proposal_ready", "adopting", "adopted", "cleaned", "stopped", "cancel_requested", "failed", "unknown")),
        ("learning_hypothesis", HYPOTHESIS_STATES),
        ("agency_execution", tuple(item.value for item in ExecutionState)),
        ("approval", APPROVAL_STATES),
        ("plan_task", TASK_STATUSES),
        ("feedback_loop", LOOP_TERMINAL_STATES),
        ("progress_task", PROGRESS_STATES),
        ("engineering_gate", ENGINEERING_GATE_STATUSES),
        ("remediation", REMEDIATION_STATUSES),
        ("goal_planning", ("open", "active", "blocked", "complete", "abandoned")),
        ("worktree", ("creating", "active", "creation_failed", "cleaned")),
        ("capability", ("canary", "promotion_blocked", "promoted", "rolled_back")),
        ("invention", ("candidate", "promoted", "blocked", "rejected")),
        ("controller_task", (
            "pending", "queued", "running", "waiting_approval", "blocked_approval",
            "waiting_quota", "blocked_quota", "blocked_reconciliation", "blocked",
            "recovering", "completed", "failed", "cancelled",
        )),
        ("trigger_task", ("pending", "claimed", "dispatched", "retryable", "success", "failed", "cancelled")),
    )
    for domain, states in vocabularies:
        for state in states:
            projected = project_lifecycle(domain, state)
            assert projected["category"] != "unknown", (domain, state)
