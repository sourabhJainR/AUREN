from datetime import datetime
from pathlib import Path
import sqlite3

import pytest

from portable.organization_guard import POLICY_VERSION, GuardPolicyError, evaluate_guard_policy
from portable.guard_policy_management import (
    DurableRateLimiter,
    GuardPolicyStore,
    evaluate_managed_policy,
    resolve_policy_inheritance,
)


def policy(*rules, default_effect="allow"):
    return {"schema_version": POLICY_VERSION, "default_effect": default_effect, "rules": list(rules)}


def rule(rule_id, effect, **match):
    return {"id": rule_id, "effect": effect, **match}


def test_inheritance_accumulates_parent_denials_and_namespaces_rule_ids():
    registry = {
        "base": {"policy": policy(rule("deny-prod", "deny", resource="prod/*")), "precedence": "deny_overrides"},
        "team": {"extends": "base", "policy": policy(rule("allow-read", "allow", action="read"), default_effect="deny")},
    }
    resolved = resolve_policy_inheritance(registry, "team")
    decision = evaluate_guard_policy(resolved, {"action": "read", "resource": "prod/db"})
    assert not decision.allowed
    assert decision.matched_rule_ids == ("base:deny-prod",)


def test_inheritance_cycles_missing_parent_and_unsupported_precedence_fail_closed():
    with pytest.raises(GuardPolicyError):
        resolve_policy_inheritance({"a": {"extends": "b", "policy": policy()}, "b": {"extends": "a", "policy": policy()}}, "a")
    with pytest.raises(GuardPolicyError):
        resolve_policy_inheritance({"a": {"extends": "missing", "policy": policy()}}, "a")
    with pytest.raises(GuardPolicyError):
        resolve_policy_inheritance({"a": {"policy": policy(), "precedence": "last_rule_wins"}}, "a")


def test_timezone_schedule_is_evaluated_in_named_zone():
    configured = {**policy(rule("deny-publish", "deny", action="publish")), "timezone": "Asia/Kolkata", "hours_local": [9], "weekdays_local": [6]}
    inside = evaluate_managed_policy(configured, {"action": "publish"}, now=datetime.fromisoformat("2026-10-25T09:30:00+05:30"))
    outside = evaluate_managed_policy(configured, {"action": "publish"}, now=datetime.fromisoformat("2026-10-25T11:30:00+05:30"))
    assert not inside.allowed
    assert outside.allowed


def test_invalid_timezone_schedule_fails_closed():
    configured = {**policy(rule("allow-publish", "allow", action="publish")), "timezone": "../unsafe"}
    with pytest.raises(GuardPolicyError):
        evaluate_managed_policy(configured, {"action": "publish"}, now=datetime.fromisoformat("2026-10-25T09:30:00+00:00"))


def test_durable_rate_limit_survives_reconstruction_and_denies_over_limit(tmp_path: Path):
    db = tmp_path / "rate.sqlite3"
    first = DurableRateLimiter(db)
    assert first.consume("org:team:publish", limit=2, window_seconds=60, now=1000)
    assert first.consume("org:team:publish", limit=2, window_seconds=60, now=1001)
    resumed = DurableRateLimiter(db)
    assert not resumed.consume("org:team:publish", limit=2, window_seconds=60, now=1002)
    assert resumed.consume("org:team:publish", limit=2, window_seconds=60, now=1062)


def test_rate_limit_configuration_missing_storage_or_invalid_limits_deny(tmp_path: Path):
    configured = policy(rule("allow-publish", "allow", action="publish"))
    decision = evaluate_managed_policy(configured, {"action": "publish"}, rate_limit={"key": "k", "limit": 1, "window_seconds": 10})
    assert not decision.allowed
    assert "not configured" in decision.reason
    limiter = DurableRateLimiter(tmp_path / "rate.sqlite3")
    with pytest.raises(GuardPolicyError):
        limiter.consume("k", limit=0, window_seconds=10)


def test_policy_activation_requires_compare_and_swap_and_supports_rollback(tmp_path: Path):
    store = GuardPolicyStore(tmp_path / "policies.sqlite3")
    first = store.activate(policy(rule("allow-read", "allow", action="read")), expected_active=None, now=100)
    second = store.activate(policy(rule("deny-read", "deny", action="read")), expected_active=first, now=101)
    assert store.active()[0] == second
    with pytest.raises(GuardPolicyError):
        store.activate(policy(), expected_active=first, now=102)
    assert store.rollback(first, expected_active=second) == first
    assert store.active()[0] == first


def test_active_policy_integrity_or_missing_active_policy_fails_closed(tmp_path: Path):
    store = GuardPolicyStore(tmp_path / "policies.sqlite3")
    with pytest.raises(GuardPolicyError):
        store.active()
    active = store.activate(policy(rule("allow-read", "allow", action="read")), expected_active=None)
    with sqlite3.connect(store.database) as db:
        db.execute("UPDATE guard_policy_versions SET policy_json=? WHERE version_id=?", ('{"tampered":true}', active))
    with pytest.raises(GuardPolicyError):
        store.active()


def test_network_scope_is_enforced_by_canonical_guard():
    configured = policy(rule("deny-private-egress", "deny", network="prod-egress"))
    assert not evaluate_guard_policy(configured, {"network": "prod-egress"}).allowed
    assert evaluate_guard_policy(configured, {"network": "dev-egress"}).allowed
