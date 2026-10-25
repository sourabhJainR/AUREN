"""Managed organization Guard policies with bounded inheritance, durable rate limits and rollback.

This is application-layer policy enforcement. It is not an OS sandbox and does
not claim to contain arbitrary subprocesses, sockets, filesystem access or IPC.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .organization_guard import (
    GuardDecision, GuardPolicyError, POLICY_VERSION, evaluate_guard_policy,
    validate_policy,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def resolve_policy_inheritance(
    policies: Mapping[str, Mapping[str, Any]], policy_id: str, *, max_depth: int = 8
) -> dict[str, Any]:
    """Resolve named parent policies. Parent restrictions accumulate; deny wins.

    Child and parent rule IDs are namespaced to preserve audit lineage. The
    explicit precedence is deny_overrides; unsupported precedence fails closed.
    """
    if not isinstance(policies, Mapping) or not isinstance(policy_id, str) or not policy_id:
        raise GuardPolicyError("policy registry and policy ID are required")
    visiting: set[str] = set()
    cache: dict[str, dict[str, Any]] = {}

    def visit(name: str, depth: int) -> dict[str, Any]:
        if depth > max_depth:
            raise GuardPolicyError("policy inheritance depth exceeded")
        if name in visiting:
            raise GuardPolicyError("policy inheritance cycle detected")
        if name in cache:
            return cache[name]
        raw = policies.get(name)
        if not isinstance(raw, Mapping):
            raise GuardPolicyError(f"missing inherited policy: {name}")
        if set(raw) - {"policy", "extends", "precedence"}:
            raise GuardPolicyError(f"unknown managed policy fields for {name}")
        precedence = raw.get("precedence", "deny_overrides")
        if precedence != "deny_overrides":
            raise GuardPolicyError("only deny_overrides precedence is supported")
        visiting.add(name)
        merged_rules: list[dict[str, Any]] = []
        default_effect = "allow"
        parent = raw.get("extends")
        parents = [] if parent is None else ([parent] if isinstance(parent, str) else parent)
        if not isinstance(parents, list) or any(not isinstance(p, str) or not p for p in parents):
            raise GuardPolicyError("extends must be a policy ID or list of policy IDs")
        for parent_id in parents:
            resolved = visit(parent_id, depth + 1)
            merged_rules.extend(dict(rule) for rule in resolved["rules"])
            if resolved["default_effect"] == "deny":
                default_effect = "deny"
        own = validate_policy(raw.get("policy"))
        merged_rules.extend({**rule, "id": f"{name}:{rule['id']}"} for rule in own["rules"])
        if own["default_effect"] == "deny":
            default_effect = "deny"
        visiting.remove(name)
        result = validate_policy({
            "schema_version": POLICY_VERSION,
            "default_effect": default_effect,
            "rules": merged_rules,
        })
        cache[name] = result
        return result

    return visit(policy_id, 1)


def _validate_local_schedule(policy: Mapping[str, Any], context: Mapping[str, Any], now: datetime) -> bool:
    """Check optional timezone-aware local schedule at evaluation time."""
    timezone_name = policy.get("timezone")
    if timezone_name is None:
        if "hours_local" in policy or "weekdays_local" in policy:
            raise GuardPolicyError("local schedule constraints require an IANA timezone")
        return True
    if not isinstance(timezone_name, str) or not timezone_name:
        raise GuardPolicyError("timezone must be an IANA timezone name")
    try:
        local = now.astimezone(ZoneInfo(timezone_name))
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise GuardPolicyError("invalid policy timezone") from exc
    hours = policy.get("hours_local")
    weekdays = policy.get("weekdays_local")
    if hours is not None and (not isinstance(hours, list) or not hours or any(type(x) is not int or x < 0 or x > 23 for x in hours) or len(set(hours)) != len(hours)):
        raise GuardPolicyError("hours_local must be a non-empty unique list of integers from 0 to 23")
    if weekdays is not None and (not isinstance(weekdays, list) or not weekdays or any(type(x) is not int or x < 0 or x > 6 for x in weekdays) or len(set(weekdays)) != len(weekdays)):
        raise GuardPolicyError("weekdays_local must be a non-empty unique list of integers from 0 to 6")
    return (hours is None or local.hour in hours) and (weekdays is None or local.weekday() in weekdays)


class DurableRateLimiter:
    """SQLite-backed sliding-window rate limits. Storage errors deny requests."""

    def __init__(self, database: str | Path):
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS guard_rate_events (
                scope_key TEXT NOT NULL, occurred REAL NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS guard_rate_events_scope_time ON guard_rate_events(scope_key, occurred)")

    def consume(self, scope_key: str, *, limit: int, window_seconds: int, now: float | None = None) -> bool:
        if not isinstance(scope_key, str) or not scope_key or type(limit) is not int or limit < 1 or type(window_seconds) is not int or window_seconds < 1:
            raise GuardPolicyError("invalid rate limit configuration")
        current = time.time() if now is None else now
        if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(current):
            raise GuardPolicyError("invalid rate limit clock")
        try:
            with sqlite3.connect(self.database, timeout=5, isolation_level=None) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM guard_rate_events WHERE occurred < ?", (current - window_seconds,))
                count = db.execute(
                    "SELECT COUNT(*) FROM guard_rate_events WHERE scope_key=? AND occurred > ?",
                    (scope_key, current - window_seconds),
                ).fetchone()[0]
                if count >= limit:
                    db.execute("COMMIT")
                    return False
                db.execute("INSERT INTO guard_rate_events(scope_key, occurred) VALUES (?, ?)", (scope_key, current))
                db.execute("COMMIT")
                return True
        except sqlite3.Error as exc:
            raise GuardPolicyError("rate-limit persistence unavailable; denying action") from exc


class GuardPolicyStore:
    """Versioned activation with append-only snapshots and explicit rollback."""

    def __init__(self, database: str | Path):
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS guard_policy_versions (
                version_id TEXT PRIMARY KEY, policy_json TEXT NOT NULL, policy_digest TEXT NOT NULL,
                status TEXT NOT NULL, created REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS guard_policy_active (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), version_id TEXT NOT NULL)""")

    def activate(self, policy: Mapping[str, Any], *, expected_active: str | None = None, now: float | None = None) -> str:
        normalized = validate_policy(policy)
        encoded = _canonical(normalized)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        version_id = digest
        current = time.time() if now is None else now
        with sqlite3.connect(self.database, timeout=5, isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version_id FROM guard_policy_active WHERE singleton=1").fetchone()
            active = row[0] if row else None
            if active != expected_active:
                db.execute("ROLLBACK")
                raise GuardPolicyError("active policy changed; activation precondition failed")
            db.execute(
                "INSERT OR IGNORE INTO guard_policy_versions(version_id,policy_json,policy_digest,status,created) VALUES (?,?,?,?,?)",
                (version_id, encoded, digest, "validated", current),
            )
            db.execute("UPDATE guard_policy_versions SET status='inactive' WHERE status='active'")
            db.execute("UPDATE guard_policy_versions SET status='active' WHERE version_id=?", (version_id,))
            db.execute("INSERT INTO guard_policy_active(singleton,version_id) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET version_id=excluded.version_id", (version_id,))
            db.execute("COMMIT")
        return version_id

    def active(self) -> tuple[str, dict[str, Any]]:
        try:
            with sqlite3.connect(self.database) as db:
                row = db.execute("SELECT v.version_id,v.policy_json,v.policy_digest FROM guard_policy_active a JOIN guard_policy_versions v ON v.version_id=a.version_id WHERE a.singleton=1 AND v.status='active'").fetchone()
        except sqlite3.Error as exc:
            raise GuardPolicyError("cannot read active Guard policy") from exc
        if not row:
            raise GuardPolicyError("no verified active Guard policy; denying action")
        version_id, encoded, digest = row
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != digest:
            raise GuardPolicyError("active Guard policy integrity failure")
        try:
            policy = validate_policy(json.loads(encoded))
        except (ValueError, json.JSONDecodeError) as exc:
            raise GuardPolicyError("active Guard policy is invalid") from exc
        return version_id, policy

    def rollback(self, version_id: str, *, expected_active: str) -> str:
        with sqlite3.connect(self.database, timeout=5, isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT version_id FROM guard_policy_active WHERE singleton=1").fetchone()
            target = db.execute("SELECT policy_json,policy_digest FROM guard_policy_versions WHERE version_id=?", (version_id,)).fetchone()
            if not current or current[0] != expected_active or not target:
                db.execute("ROLLBACK")
                raise GuardPolicyError("rollback precondition failed")
            if hashlib.sha256(target[0].encode("utf-8")).hexdigest() != target[1]:
                db.execute("ROLLBACK")
                raise GuardPolicyError("rollback target integrity failure")
            validate_policy(json.loads(target[0]))
            db.execute("UPDATE guard_policy_versions SET status='inactive' WHERE status='active'")
            db.execute("UPDATE guard_policy_versions SET status='active' WHERE version_id=?", (version_id,))
            db.execute("UPDATE guard_policy_active SET version_id=? WHERE singleton=1", (version_id,))
            db.execute("COMMIT")
        return version_id



class GuardActionEvidenceLedger:
    """Durable hash-chained action lineage; outcomes are never inferred on resume.

    This ledger proves internal event consistency, not signer identity or trusted
    remote truth. Callers must verify provider outcomes independently before
    finalizing them and must reconcile 'pending' operations after restart.
    """

    def __init__(self, database: str | Path):
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS guard_action_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                event_json TEXT NOT NULL, previous_digest TEXT NOT NULL, digest TEXT NOT NULL)""")

    def append(self, *, event_id: str, identity: str, delegated_scope: Sequence[str],
               policy_version: str, policy_digest: str, approval_id: str | None,
               provider_operation_id: str, verified_outcome: str, event_type: str,
               timestamp: float | None = None) -> str:
        required = (event_id, identity, policy_version, policy_digest, provider_operation_id, event_type)
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise GuardPolicyError("action evidence identity fields are required")
        if not isinstance(delegated_scope, (list, tuple)) or not delegated_scope or any(not isinstance(x, str) or not x for x in delegated_scope):
            raise GuardPolicyError("delegated scope must be a non-empty list of capabilities")
        if verified_outcome not in {"pending", "succeeded", "failed", "unknown"}:
            raise GuardPolicyError("verified outcome must be pending, succeeded, failed, or unknown")
        if event_type not in {"dispatch_authorized", "provider_outcome_verified", "revoked", "reconciled"}:
            raise GuardPolicyError("unsupported action evidence event type")
        if event_type == "dispatch_authorized" and verified_outcome != "pending":
            raise GuardPolicyError("dispatch authorization must begin with pending outcome")
        if event_type == "provider_outcome_verified" and verified_outcome not in {"succeeded", "failed", "unknown"}:
            raise GuardPolicyError("provider outcome event must record an explicit terminal or unknown outcome")
        if event_type == "revoked" and verified_outcome not in {"unknown", "failed"}:
            raise GuardPolicyError("revocation cannot claim provider success")
        payload = {
            "schema_version": "auren-guard-action-evidence-v1",
            "event_id": event_id, "identity": identity,
            "delegated_scope": sorted(set(delegated_scope)),
            "policy_version": policy_version, "policy_digest": policy_digest,
            "approval_id": approval_id, "provider_operation_id": provider_operation_id,
            "verified_outcome": verified_outcome, "event_type": event_type,
            "timestamp": time.time() if timestamp is None else timestamp,
        }
        if isinstance(payload["timestamp"], bool) or not isinstance(payload["timestamp"], (int, float)):
            raise GuardPolicyError("invalid evidence timestamp")
        encoded = _canonical(payload)
        try:
            with sqlite3.connect(self.database, timeout=5, isolation_level=None) as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT event_json,digest FROM guard_action_events WHERE event_id=?", (event_id,)).fetchone()
                if existing:
                    db.execute("ROLLBACK")
                    if existing[0] == encoded and self.verify():
                        return existing[1]
                    raise GuardPolicyError("evidence event ID replayed with different content")
                previous = db.execute("SELECT digest FROM guard_action_events ORDER BY sequence DESC LIMIT 1").fetchone()
                previous_digest = previous[0] if previous else "0" * 64
                digest = hashlib.sha256((previous_digest + encoded).encode("utf-8")).hexdigest()
                db.execute("INSERT INTO guard_action_events(event_id,event_json,previous_digest,digest) VALUES (?,?,?,?)",
                           (event_id, encoded, previous_digest, digest))
                db.execute("COMMIT")
                return digest
        except sqlite3.Error as exc:
            raise GuardPolicyError("action evidence persistence failed; dispatch must remain blocked") from exc

    def verify(self) -> bool:
        try:
            with sqlite3.connect(self.database) as db:
                rows = db.execute("SELECT event_id,event_json,previous_digest,digest FROM guard_action_events ORDER BY sequence").fetchall()
        except sqlite3.Error as exc:
            raise GuardPolicyError("action evidence ledger unavailable") from exc
        previous = "0" * 64
        seen: set[str] = set()
        for event_id, encoded, stored_previous, stored_digest in rows:
            if event_id in seen or stored_previous != previous:
                return False
            try:
                payload = json.loads(encoded)
            except (TypeError, json.JSONDecodeError):
                return False
            if payload.get("event_id") != event_id:
                return False
            expected = hashlib.sha256((previous + _canonical(payload)).encode("utf-8")).hexdigest()
            if expected != stored_digest:
                return False
            previous = stored_digest
            seen.add(event_id)
        return True

    def unresolved_operations(self) -> list[dict[str, Any]]:
        """Return latest per-operation state; pending/unknown must be reconciled."""
        if not self.verify():
            raise GuardPolicyError("action evidence chain integrity failure; refusing recovery")
        with sqlite3.connect(self.database) as db:
            rows = db.execute("SELECT event_json FROM guard_action_events ORDER BY sequence").fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for (encoded,) in rows:
            event = json.loads(encoded)
            latest[event["provider_operation_id"]] = event
        return [event for event in latest.values() if event["verified_outcome"] in {"pending", "unknown"}]

\ndef evaluate_managed_policy(
    policy: Mapping[str, Any], context: Mapping[str, Any], *,
    rate_limiter: DurableRateLimiter | None = None,
    rate_limit: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    mandatory_authorized: bool = True,
    approval_gate_configured: bool = False,
) -> GuardDecision:
    """Evaluate scoped policy and rate limit before a caller dispatches work."""
    if not mandatory_authorized:
        return evaluate_guard_policy(policy, context, mandatory_authorized=False, approval_gate_configured=approval_gate_configured)
    current = now or datetime.now().astimezone()
    if not _validate_local_schedule(policy, context, current):
        digest = hashlib.sha256(_canonical(policy).encode("utf-8")).hexdigest()
        return GuardDecision(False, "deny", "organization policy local-time schedule does not match", digest)
    # Resource/network scopes are ordinary exact/glob policy match fields and
    # are enforced by the canonical evaluator after context normalization.
    base_policy = {key: value for key, value in policy.items() if key not in {"timezone", "hours_local", "weekdays_local"}}\n    decision = evaluate_guard_policy(base_policy, context, mandatory_authorized=True, approval_gate_configured=approval_gate_configured)
    if not decision.allowed or rate_limit is None:
        return decision
    if rate_limiter is None:
        return GuardDecision(False, "deny", "rate limiter is not configured; denying action", decision.policy_digest, decision.matched_rule_ids)
    if set(rate_limit) != {"key", "limit", "window_seconds"} or not isinstance(rate_limit["key"], str) or not rate_limit["key"]:
        raise GuardPolicyError("rate_limit requires key, limit, and window_seconds")
    if not rate_limiter.consume(rate_limit["key"], limit=rate_limit["limit"], window_seconds=rate_limit["window_seconds"]):
        return GuardDecision(False, "deny", "organization policy rate limit exceeded", decision.policy_digest, decision.matched_rule_ids)
    return decision


__all__ = [
    "DurableRateLimiter", "GuardPolicyStore", "GuardActionEvidenceLedger", "evaluate_managed_policy",
    "resolve_policy_inheritance",
]
