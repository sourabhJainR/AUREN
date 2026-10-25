from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

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


class GuardPolicyManagementTests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temporary_directory.name)

    def tearDown(self):
        self._temporary_directory.cleanup()

    def test_inheritance_accumulates_parent_denials_and_namespaces_rule_ids(self):
        registry = {
            "base": {"policy": policy(rule("deny-prod", "deny", resource="prod/*")), "precedence": "deny_overrides"},
            "team": {"extends": "base", "policy": policy(rule("allow-read", "allow", action="read"), default_effect="deny")},
        }
        resolved = resolve_policy_inheritance(registry, "team")
        decision = evaluate_guard_policy(resolved, {"action": "read", "resource": "prod/db"})
        assert not decision.allowed
        assert decision.matched_rule_ids == ("base:deny-prod",)


    def test_inheritance_cycles_missing_parent_and_unsupported_precedence_fail_closed(self):
        with self.assertRaises(GuardPolicyError):
            resolve_policy_inheritance({"a": {"extends": "b", "policy": policy()}, "b": {"extends": "a", "policy": policy()}}, "a")
        with self.assertRaises(GuardPolicyError):
            resolve_policy_inheritance({"a": {"extends": "missing", "policy": policy()}}, "a")
        with self.assertRaises(GuardPolicyError):
            resolve_policy_inheritance({"a": {"policy": policy(), "precedence": "last_rule_wins"}}, "a")


    def test_timezone_schedule_is_evaluated_in_named_zone(self):
        configured = {**policy(rule("deny-publish", "deny", action="publish")), "timezone": "Asia/Kolkata", "hours_local": [9], "weekdays_local": [6]}
        inside = evaluate_managed_policy(configured, {"action": "publish"}, now=datetime.fromisoformat("2026-10-25T09:30:00+05:30"))
        outside = evaluate_managed_policy(configured, {"action": "publish"}, now=datetime.fromisoformat("2026-10-25T11:30:00+05:30"))
        assert not inside.allowed
        assert not outside.allowed
        assert "schedule does not match" in outside.reason


    def test_invalid_timezone_schedule_fails_closed(self):
        configured = {**policy(rule("allow-publish", "allow", action="publish")), "timezone": "../unsafe"}
        with self.assertRaises(GuardPolicyError):
            evaluate_managed_policy(configured, {"action": "publish"}, now=datetime.fromisoformat("2026-10-25T09:30:00+00:00"))


    def test_durable_rate_limit_survives_reconstruction_and_denies_over_limit(self):
        db = self.tmp_path / "rate.sqlite3"
        first = DurableRateLimiter(db)
        assert first.consume("org:team:publish", limit=2, window_seconds=60, now=1000)
        assert first.consume("org:team:publish", limit=2, window_seconds=60, now=1001)
        resumed = DurableRateLimiter(db)
        assert not resumed.consume("org:team:publish", limit=2, window_seconds=60, now=1002)
        assert resumed.consume("org:team:publish", limit=2, window_seconds=60, now=1062)


    def test_rate_limit_configuration_missing_storage_or_invalid_limits_deny(self):
        configured = policy(rule("allow-publish", "allow", action="publish"))
        decision = evaluate_managed_policy(configured, {"action": "publish"}, rate_limit={"key": "k", "limit": 1, "window_seconds": 10})
        assert not decision.allowed
        assert "not configured" in decision.reason
        limiter = DurableRateLimiter(self.tmp_path / "rate.sqlite3")
        with self.assertRaises(GuardPolicyError):
            limiter.consume("k", limit=0, window_seconds=10)


    def test_policy_activation_requires_compare_and_swap_and_supports_rollback(self):
        store = GuardPolicyStore(self.tmp_path / "policies.sqlite3")
        first = store.activate(policy(rule("allow-read", "allow", action="read")), expected_active=None, now=100)
        second = store.activate(policy(rule("deny-read", "deny", action="read")), expected_active=first, now=101)
        assert store.active()[0] == second
        with self.assertRaises(GuardPolicyError):
            store.activate(policy(), expected_active=first, now=102)
        assert store.rollback(first, expected_active=second) == first
        assert store.active()[0] == first


    def test_active_policy_integrity_or_missing_active_policy_fails_closed(self):
        store = GuardPolicyStore(self.tmp_path / "policies.sqlite3")
        with self.assertRaises(GuardPolicyError):
            store.active()
        active = store.activate(policy(rule("allow-read", "allow", action="read")), expected_active=None)
        with sqlite3.connect(store.database) as db:
            db.execute("UPDATE guard_policy_versions SET policy_json=? WHERE version_id=?", ('{"tampered":true}', active))
        with self.assertRaises(GuardPolicyError):
            store.active()


    def test_network_scope_is_enforced_by_canonical_guard(self):
        configured = policy(rule("deny-private-egress", "deny", network="prod-egress"))
        assert not evaluate_guard_policy(configured, {"network": "prod-egress"}).allowed
        assert evaluate_guard_policy(configured, {"network": "dev-egress"}).allowed


    def test_action_evidence_binds_identity_scope_policy_approval_and_provider_outcome(self):
        from portable.guard_policy_management import GuardActionEvidenceLedger

        ledger = GuardActionEvidenceLedger(self.tmp_path / "evidence.sqlite3")
        authorized = dict(
            event_id="op-1:dispatch", identity="org:acme/agent:worker",
            delegated_scope=("file.read", "file.write"), policy_version="policy-v7",
            policy_digest="a" * 64, approval_id="approval-42",
            provider_operation_id="provider-op-9", verified_outcome="pending",
            event_type="dispatch_authorized", timestamp=100,
        )
        digest = ledger.append(**authorized)
        assert ledger.verify()
        assert ledger.append(**authorized) == digest
        assert [item["provider_operation_id"] for item in ledger.unresolved_operations()] == ["provider-op-9"]
        ledger.append(
            **{**authorized, "event_id": "op-1:outcome", "verified_outcome": "succeeded",
               "event_type": "provider_outcome_verified", "timestamp": 101},
        )
        assert ledger.verify()
        assert ledger.unresolved_operations() == []


    def test_action_evidence_rejects_event_id_replay_with_changed_authority(self):
        from portable.guard_policy_management import GuardActionEvidenceLedger

        ledger = GuardActionEvidenceLedger(self.tmp_path / "evidence.sqlite3")
        event = dict(
            event_id="op-1:dispatch", identity="org:acme/agent:worker",
            delegated_scope=("file.read",), policy_version="policy-v7",
            policy_digest="b" * 64, approval_id=None, provider_operation_id="remote-1",
            verified_outcome="pending", event_type="dispatch_authorized", timestamp=10,
        )
        ledger.append(**event)
        with self.assertRaises(GuardPolicyError):
            ledger.append(**{**event, "delegated_scope": ("admin",)})
        assert ledger.verify()


    def test_corrupt_action_evidence_chain_blocks_recovery(self):
        from portable.guard_policy_management import GuardActionEvidenceLedger

        ledger = GuardActionEvidenceLedger(self.tmp_path / "evidence.sqlite3")
        ledger.append(
            event_id="op:dispatch", identity="worker", delegated_scope=("tool.read",),
            policy_version="v1", policy_digest="c" * 64, approval_id=None,
            provider_operation_id="provider-1", verified_outcome="pending",
            event_type="dispatch_authorized", timestamp=10,
        )
        import sqlite3
        with sqlite3.connect(ledger.database) as db:
            db.execute("UPDATE guard_action_events SET event_json=? WHERE event_id=?", ('{"event_id":"op:dispatch"}', "op:dispatch"))
        assert not ledger.verify()
        with self.assertRaises(GuardPolicyError):
            ledger.unresolved_operations()


    def test_local_schedule_without_timezone_fails_closed(self):
        configured = {**policy(rule("deny-publish", "deny", action="publish")), "hours_local": [9]}
        with self.assertRaises(GuardPolicyError):
            evaluate_managed_policy(configured, {"action": "publish"}, now=datetime.fromisoformat("2026-10-25T09:00:00+00:00"))


    def test_action_evidence_does_not_allow_dispatch_receipt_to_claim_success(self):
        from portable.guard_policy_management import GuardActionEvidenceLedger

        ledger = GuardActionEvidenceLedger(self.tmp_path / "evidence.sqlite3")
        with self.assertRaises(GuardPolicyError):
            ledger.append(
                event_id="bad:dispatch", identity="worker", delegated_scope=("tool.read",),
                policy_version="v1", policy_digest="d" * 64, approval_id=None,
                provider_operation_id="provider-2", verified_outcome="succeeded",
                event_type="dispatch_authorized", timestamp=10,
            )


    def test_action_evidence_rejects_non_finite_timestamps(self):
        from portable.guard_policy_management import GuardActionEvidenceLedger

        ledger = GuardActionEvidenceLedger(self.tmp_path / "evidence.sqlite3")
        base = dict(
            event_id="invalid-time", identity="worker", delegated_scope=("tool.read",),
            policy_version="v1", policy_digest="e" * 64, approval_id=None,
            provider_operation_id="provider-time", verified_outcome="pending",
            event_type="dispatch_authorized",
        )
        for timestamp in (float("nan"), float("inf"), float("-inf"), True):
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(GuardPolicyError):
                    ledger.append(**base, timestamp=timestamp)
        assert ledger.verify()

    def test_action_evidence_verification_rejects_non_object_json_without_crashing(self):
        from portable.guard_policy_management import GuardActionEvidenceLedger

        ledger = GuardActionEvidenceLedger(self.tmp_path / "evidence.sqlite3")
        ledger.append(
            event_id="op:dispatch", identity="worker", delegated_scope=("tool.read",),
            policy_version="v1", policy_digest="f" * 64, approval_id=None,
            provider_operation_id="provider-malformed", verified_outcome="pending",
            event_type="dispatch_authorized", timestamp=10,
        )
        with sqlite3.connect(ledger.database) as db:
            db.execute(
                "UPDATE guard_action_events SET event_json=? WHERE event_id=?",
                ('["not", "an", "event"]', "op:dispatch"),
            )
        assert not ledger.verify()
        with self.assertRaises(GuardPolicyError):
            ledger.unresolved_operations()
