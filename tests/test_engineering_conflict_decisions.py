from portable.engineering_design_guard import EngineeringDesignGuard


def test_conflict_review_preserves_source_statements_impact_options_and_recommendation():
    review = EngineeringDesignGuard.review_request(
        intent="Plan a public API update",
        requirements=(
            "Preserve backward compatibility for existing API consumers.",
            "Introduce a breaking change to remove the legacy API.",
        ),
        acceptance_criteria=("New API behavior is documented.",),
    )

    assert review["implementation_may_start"] is False
    conflict = review["conflict_details"][0]
    assert conflict["id"] == "compatibility-vs-breaking-change"
    assert conflict["requirement_a"]["statement"] == "Preserve backward compatibility for existing API consumers."
    assert conflict["requirement_b"]["statement"] == "Introduce a breaking change to remove the legacy API."
    assert conflict["impact"]
    assert len(conflict["options"]) == 2
    assert all(item["tradeoffs"] for item in conflict["options"])
    assert conflict["recommended_option"]
    assert conflict["decision_required"] is True


def test_explicit_conflict_resolution_is_recorded_but_not_silently_inferred():
    review = EngineeringDesignGuard.review_request(
        intent="Plan a public API update",
        requirements=(
            "Preserve backward compatibility for existing API consumers.",
            "Introduce a breaking change to remove the legacy API.",
        ),
        resolutions={"compatibility-vs-breaking-change": "Use a versioned replacement API."},
    )

    conflict = review["conflict_details"][0]
    assert conflict["status"] == "resolved"
    assert conflict["resolution"] == "Use a versioned replacement API."
    assert conflict["decision_required"] is False
    assert review["implementation_may_start"] is True


def test_reviewer_conflict_without_source_statements_requires_exact_decision():
    review = EngineeringDesignGuard.review_request(
        intent="Deliver the application",
        candidate_contradictions=("Live provider data conflicts with offline-only deployment.",),
    )

    conflict = review["conflict_details"][0]
    assert conflict["requirement_a"] is None
    assert conflict["requirement_b"] is None
    assert "source statements" in conflict["recommended_option"]
    assert conflict["decision_required"] is True
    assert review["implementation_may_start"] is False
