import pytest
from portable.requirement_conflicts import RequirementConflictAnalyzer, RequirementFact

@pytest.mark.parametrize("key,left,right", [
    ("market_data_latency", "real_time", "delayed"),
    ("data_redistribution", "public_redistribution", "prohibited"),
    ("user_state", "no_accounts", "per_user_saved_state"),
    ("recommendation_approval", "fully_automated", "human_approval_required"),
    ("data_retention", "no_external_storage", "historical_analytics"),
    ("delivery_scope", "fast_mvp", "broad_coverage_ha_compliance"),
])
def test_known_material_requirement_conflicts_require_decision(key, left, right):
    report = RequirementConflictAnalyzer.analyze([
        RequirementFact("user-a", key, left, f"First requirement: {left}"),
        RequirementFact("user-b", key, right, f"Second requirement: {right}"),
    ])
    assert report.status == "decision_required"
    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert conflict.key == key and conflict.decision_required
    assert {item.value for item in conflict.statements} == {left, right}
    assert conflict.recommended_option in {item.option_id for item in conflict.options}

def test_only_dependent_workstreams_are_blocked():
    report = RequirementConflictAnalyzer.analyze([
        RequirementFact("r1", "market_data_latency", "real_time", "Quotes must be real time"),
        RequirementFact("r2", "market_data_latency", "delayed", "Only delayed data is available"),
    ], workstream_dependencies={
        "quote-adapter": ("market_data_latency",),
        "news-ingestion": ("news",), "ui-shell": ("layout",),
    })
    assert report.blocked_workstreams == ("quote-adapter",)
    assert report.independent_workstreams == ("news-ingestion", "ui-shell")

def test_no_conflict_does_not_invent_a_decision():
    report = RequirementConflictAnalyzer.analyze([
        RequirementFact("r1", "market_data_latency", "real_time", "Quotes must be real time"),
        RequirementFact("r2", "data_redistribution", "prohibited", "Provider prohibits redistribution"),
    ])
    assert report.status == "clear" and report.conflicts == ()

def test_statement_ids_must_be_unique():
    with pytest.raises(ValueError, match="unique"):
        RequirementConflictAnalyzer.analyze([
            RequirementFact("same", "market_data_latency", "real_time", "A"),
            RequirementFact("same", "market_data_latency", "delayed", "B"),
        ])

def test_decision_record_binds_choice_to_plan_revision():
    conflict = RequirementConflictAnalyzer.analyze([
        RequirementFact("r1", "user_state", "no_accounts", "No accounts"),
        RequirementFact("r2", "user_state", "per_user_saved_state", "Each user saves portfolios"),
    ]).conflicts[0]
    decision = RequirementConflictAnalyzer.record_decision(
        conflict, selected_option="local_only", decision_maker="product-owner",
        rationale="Keep the first release account-free and local to each device.",
        prior_plan_digest="a" * 64, new_plan_revision=2)
    assert decision.conflict_id == conflict.conflict_id
    assert decision.prior_plan_digest == "a" * 64 and decision.new_plan_revision == 2
    assert len(decision.digest) == 64
    with pytest.raises(ValueError, match="explicit options"):
        RequirementConflictAnalyzer.record_decision(
            conflict, selected_option="silent_guess", decision_maker="product-owner",
            rationale="No approval", prior_plan_digest="a" * 64, new_plan_revision=2)

def test_conflict_identity_changes_when_source_statement_changes():
    def detect(second):
        return RequirementConflictAnalyzer.analyze([
            RequirementFact("r1", "recommendation_approval", "fully_automated", "Automate publication"),
            RequirementFact("r2", "recommendation_approval", "human_approval_required", second),
        ]).conflicts[0].conflict_id
    assert detect("Require approval") != detect("Require investment committee approval")
