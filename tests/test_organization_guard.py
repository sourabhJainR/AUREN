import json
from datetime import datetime, timezone, timedelta
from pathlib import Path


from portable.organization_guard import (
    POLICY_VERSION,
    _time_conditions_match,
    GuardPolicyError,
    evaluate_guard_policy,
    load_guard_policy,
    validate_policy,
)


def policy(*rules, default_effect="allow"):
    return {"schema_version": POLICY_VERSION, "default_effect": default_effect, "rules": list(rules)}


def rule(rule_id, effect, **match):
    return {"id": rule_id, "effect": effect, **match}


def test_deny_overrides_allow_independent_of_rule_order():
    context = {"action": "push", "tool": "github"}
    for rules in (
        [rule("allow-push", "allow", action="push"), rule("deny-push", "deny", tool="github")],
        [rule("deny-push", "deny", tool="github"), rule("allow-push", "allow", action="push")],
    ):
        decision = evaluate_guard_policy(policy(*rules), context)
        assert not decision.allowed
        assert decision.effect == "deny"
        assert decision.matched_rule_ids == ("deny-push",)


def test_default_deny_requires_explicit_matching_allow():
    configured = policy(rule("allow-read", "allow", action="read"), default_effect="deny")
    assert evaluate_guard_policy(configured, {"action": "read"}).allowed
    denied = evaluate_guard_policy(configured, {"action": "delete"})
    assert not denied.allowed
    assert "default-deny" in denied.reason


def test_organization_approval_rule_blocks_before_dispatch():
    configured = policy(rule("approval-release", "require_approval", action="release"))
    decision = evaluate_guard_policy(configured, {"action": "release"})
    assert not decision.allowed
    assert decision.requires_approval
    assert decision.effect == "require_approval"
    approved_path = evaluate_guard_policy(configured, {"action": "release"}, approval_gate_configured=True)
    assert approved_path.allowed
    assert approved_path.effect == "require_approval"
    assert approved_path.requires_approval


def test_organization_allow_never_overrides_mandatory_authority():
    configured = policy(rule("allow-all", "allow", action="*"))
    decision = evaluate_guard_policy(configured, {"action": "merge"}, mandatory_authorized=False)
    assert not decision.allowed
    assert decision.effect == "deny"


def test_policy_digest_and_receipt_are_deterministic():
    configured = policy(rule("deny-tool", "deny", tool="shell"))
    first = evaluate_guard_policy(configured, {"tool": "shell", "action": "run"})
    second = evaluate_guard_policy(configured, {"action": "run", "tool": "shell"})
    assert first.policy_digest == second.policy_digest
    assert first.as_receipt() == second.as_receipt()


def test_invalid_policy_unknown_fields_and_duplicate_ids_fail_closed():
    import pytest
    with pytest.raises(GuardPolicyError):
        validate_policy({**policy(), "bypass_guard": True})
    with pytest.raises(GuardPolicyError):
        validate_policy(policy(rule("same", "deny", action="run"), rule("same", "allow", action="run")))


def test_loader_rejects_duplicate_json_keys_and_symlink(tmp_path: Path):
    import pytest
    configured = tmp_path / "guard.json"
    configured.write_text('{"schema_version":"auren-guard-v1","schema_version":"auren-guard-v1","rules":[]}', encoding="utf-8")
    with pytest.raises(GuardPolicyError):
        load_guard_policy(configured)
    target = tmp_path / "real.json"
    target.write_text(json.dumps(policy()), encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(GuardPolicyError):
        load_guard_policy(link)


def test_missing_policy_is_distinct_and_uses_safe_baseline(tmp_path: Path):
    assert load_guard_policy(tmp_path / "missing.json") is None
    decision = evaluate_guard_policy(None, {"action": "read"})
    assert decision.allowed
    assert len(decision.policy_digest) == 64

def test_scoped_globs_and_malformed_types_are_handled_safely():
    import pytest
    configured = policy(rule("deny-prod", "deny", resource="prod/*"))
    assert not evaluate_guard_policy(configured, {"resource": "prod/customer-7"}).allowed
    assert evaluate_guard_policy(configured, {"resource": "dev/customer-7"}).allowed
    with pytest.raises(GuardPolicyError):
        validate_policy({"schema_version": POLICY_VERSION, "default_effect": [], "rules": []})
    with pytest.raises(GuardPolicyError):
        validate_policy(policy(rule("bad-effect", [], action="read")))


def test_approval_requirement_never_substitutes_for_default_deny_allow():
    configured = policy(
        rule("approval-release", "require_approval", action="release"),
        default_effect="deny",
    )
    decision = evaluate_guard_policy(
        configured, {"action": "release"}, approval_gate_configured=True,
    )
    assert not decision.allowed
    assert decision.effect == "deny"
    assert "default-deny" in decision.reason

    explicitly_allowed = policy(
        rule("allow-release", "allow", action="release"),
        rule("approval-release", "require_approval", action="release"),
        default_effect="deny",
    )
    approved_path = evaluate_guard_policy(
        explicitly_allowed, {"action": "release"}, approval_gate_configured=True,
    )
    assert approved_path.allowed
    assert approved_path.effect == "require_approval"


def test_question_mark_glob_is_rejected_when_only_star_wildcards_are_supported():
    import pytest
    with pytest.raises(GuardPolicyError):
        validate_policy(policy(rule("unsupported-pattern", "deny", resource="prod/?")))


def test_guard_rules_support_utc_hour_and_weekday_constraints():
    now = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)
    condition = {"hours_utc": [9, 10], "weekdays_utc": [6]}
    assert _time_conditions_match(condition, now)
    assert not _time_conditions_match(condition, now + timedelta(hours=2))
    assert not _time_conditions_match(condition, now.replace(hour=11))
    assert not _time_conditions_match(condition, now.replace(day=26))


def test_guard_time_constraints_validate_strictly():
    import pytest
    with pytest.raises(GuardPolicyError):
        validate_policy(policy(rule("bad-hour", "deny", action="publish", hours_utc=[24])))
    with pytest.raises(GuardPolicyError):
        validate_policy(policy(rule("bad-day", "deny", action="publish", weekdays_utc=[7])))
    with pytest.raises(GuardPolicyError):
        validate_policy(policy(rule("duplicate-hour", "deny", action="publish", hours_utc=[9, 9])))
    with pytest.raises(GuardPolicyError):
        validate_policy(policy(rule("empty-time-rule", "deny", hours_utc=[])))
