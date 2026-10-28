"""Organization-configurable Guard policy evaluation for AUREN.

Organization policy can add restrictions, but cannot grant authority that the
execution controller, approval gateway, or repository policy does not provide.
Missing or invalid configured policy fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from fnmatch import fnmatchcase
import ipaddress
from pathlib import Path
from typing import Any, Mapping

POLICY_VERSION = "auren-guard-v1"
MAX_POLICY_BYTES = 64 * 1024
_ALLOWED_EFFECTS = frozenset({"allow", "deny", "require_approval"})
_MATCH_FIELDS = frozenset({
    "action", "tool", "resource", "network", "organization_id", "agent_id", "risk",
})
_POLICY_FIELDS = frozenset({"schema_version", "default_effect", "rules"})
_RULE_FIELDS = frozenset({"id", "effect", "hours_utc", "weekdays_utc", "network_cidr", *_MATCH_FIELDS})


class GuardPolicyError(ValueError):
    """Raised for unsafe, malformed, or incompatible Guard policy."""


@dataclass(frozen=True, slots=True)
class GuardDecision:
    allowed: bool
    effect: str
    reason: str
    policy_digest: str
    matched_rule_ids: tuple[str, ...] = ()
    requires_approval: bool = False

    def as_receipt(self) -> dict[str, Any]:
        return {
            "schema_version": POLICY_VERSION,
            "allowed": self.allowed,
            "effect": self.effect,
            "reason": self.reason,
            "policy_digest": self.policy_digest,
            "matched_rule_ids": list(self.matched_rule_ids),
            "requires_approval": self.requires_approval,
        }


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GuardPolicyError(f"duplicate policy field: {key}")
        result[key] = value
    return result


def validate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GuardPolicyError("Guard policy must be an object")
    unknown = set(value) - _POLICY_FIELDS
    if unknown:
        raise GuardPolicyError("unknown Guard policy fields: " + ", ".join(sorted(unknown)))
    if value.get("schema_version") != POLICY_VERSION:
        raise GuardPolicyError("unsupported Guard policy schema version")
    default_effect = value.get("default_effect", "allow")
    if not isinstance(default_effect, str) or default_effect not in {"allow", "deny"}:
        raise GuardPolicyError("default_effect must be allow or deny")
    rules = value.get("rules", [])
    if not isinstance(rules, list) or len(rules) > 500:
        raise GuardPolicyError("rules must be a list of at most 500 entries")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            raise GuardPolicyError(f"rule {index} must be an object")
        extra = set(rule) - _RULE_FIELDS
        if extra:
            raise GuardPolicyError(f"unknown fields in rule {index}: " + ", ".join(sorted(extra)))
        rule_id, effect = rule.get("id"), rule.get("effect")
        if not isinstance(rule_id, str) or not rule_id.strip() or len(rule_id) > 128 or rule_id in seen_ids:
            raise GuardPolicyError(f"rule {index} requires a unique non-empty id")
        if not isinstance(effect, str) or effect not in _ALLOWED_EFFECTS:
            raise GuardPolicyError(f"rule {rule_id} has an invalid effect")
        item: dict[str, Any] = {"id": rule_id, "effect": effect}
        for field in _MATCH_FIELDS:
            if field in rule:
                matcher = rule[field]
                if (
                    not isinstance(matcher, str) or not matcher or len(matcher) > 512
                    or not matcher.isprintable() or any(char in matcher for char in "[]?")
                ):
                    raise GuardPolicyError(f"rule {rule_id} has an invalid {field} matcher")
                item[field] = matcher
        if "network_cidr" in rule:
            cidr = rule["network_cidr"]
            if not isinstance(cidr, str) or not cidr or len(cidr) > 128:
                raise GuardPolicyError(f"rule {rule_id} has an invalid network_cidr")
            try:
                item["network_cidr"] = str(ipaddress.ip_network(cidr, strict=False))
            except ValueError as exc:
                raise GuardPolicyError(f"rule {rule_id} has an invalid network_cidr") from exc
        for field, maximum in (("hours_utc", 23), ("weekdays_utc", 6)):
            if field in rule:
                values = rule[field]
                if (
                    not isinstance(values, list) or not values or len(values) > maximum + 1
                    or any(type(value) is not int or value < 0 or value > maximum for value in values)
                    or len(set(values)) != len(values)
                ):
                    raise GuardPolicyError(
                        f"rule {rule_id} {field} must be a non-empty unique list of integers from 0 to {maximum}"
                    )
                item[field] = sorted(values)
        if not any(field in item for field in _MATCH_FIELDS) and "network_cidr" not in item and not any(
            field in item for field in ("hours_utc", "weekdays_utc")
        ):
            raise GuardPolicyError(f"rule {rule_id} must match at least one context field or UTC time condition")
        normalized.append(item)
        seen_ids.add(rule_id)
    return {"schema_version": POLICY_VERSION, "default_effect": default_effect, "rules": normalized}


def guard_policy_fingerprint(policy: Mapping[str, Any] | None) -> str:
    """Return the stable digest of the effective Guard policy, including defaults."""
    normalized = (
        {"schema_version": POLICY_VERSION, "default_effect": "allow", "rules": []}
        if policy is None else validate_policy(policy)
    )
    return hashlib.sha256(_canonical(normalized).encode("utf-8")).hexdigest()


def load_guard_policy(path: str | Path) -> dict[str, Any] | None:
    """Load organization policy. Missing is distinct from malformed or unsafe."""
    target = Path(path).expanduser()
    try:
        if target.is_symlink() or target.parent.is_symlink():
            raise GuardPolicyError("refusing symlink Guard policy path")
        with target.open("rb") as handle:
            raw = handle.read(MAX_POLICY_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GuardPolicyError("cannot read configured Guard policy") from exc
    if len(raw) > MAX_POLICY_BYTES:
        raise GuardPolicyError("Guard policy exceeds size limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardPolicyError("configured Guard policy is malformed") from exc
    return validate_policy(value)


def _time_conditions_match(rule: Mapping[str, Any], current: datetime) -> bool:
    """Apply optional UTC hour/weekday constraints using a trusted clock."""
    if current.tzinfo is None:
        raise GuardPolicyError("Guard evaluation time must be timezone-aware")
    current_utc = current.astimezone(timezone.utc)
    hours = rule.get("hours_utc")
    weekdays = rule.get("weekdays_utc")
    if hours is not None and current_utc.hour not in hours:
        return False
    if weekdays is not None and current_utc.weekday() not in weekdays:
        return False
    return True


def evaluate_guard_policy(
    policy: Mapping[str, Any] | None,
    context: Mapping[str, Any],
    *,
    mandatory_authorized: bool = True,
    approval_gate_configured: bool = False,
) -> GuardDecision:
    """Evaluate organization rules without allowing them to override mandatory gates.

    Deny overrides allow regardless of rule order. Approval requirements are
    enforced before dispatch. Unknown context values never match a wildcard by
    accident; only explicit strings are compared.
    """
    if not isinstance(context, Mapping):
        raise GuardPolicyError("Guard context must be a mapping")
    safe_context: dict[str, str] = {}
    for field in _MATCH_FIELDS:
        value = context.get(field)
        if isinstance(value, str) and value and value.isprintable():
            safe_context[field] = value
    network_ip = context.get("network_ip")
    if isinstance(network_ip, str) and network_ip and network_ip.isprintable():
        safe_context["network_ip"] = network_ip
    if policy is None:
        normalized = {"schema_version": POLICY_VERSION, "default_effect": "allow", "rules": []}
    else:
        normalized = validate_policy(policy)
    cidr_rules = [rule for rule in normalized["rules"] if "network_cidr" in rule]
    if cidr_rules:
        network_ip = safe_context.get("network_ip")
        try:
            ipaddress.ip_address(network_ip or "")
        except ValueError as exc:
            raise GuardPolicyError("a valid network_ip context is required by network_cidr policy") from exc
    digest = hashlib.sha256(_canonical(normalized).encode("utf-8")).hexdigest()
    if not mandatory_authorized:
        return GuardDecision(False, "deny", "mandatory platform or execution-controller authorization is missing", digest)
    matched: list[Mapping[str, Any]] = []
    current_utc = datetime.now(timezone.utc)
    for rule in normalized["rules"]:
        if not _time_conditions_match(rule, current_utc):
            continue
        field_match = all(
            key in safe_context and (
                rule[key] == safe_context[key]
                or ("*" in rule[key] and fnmatchcase(safe_context[key], rule[key]))
            )
            for key in rule if key in _MATCH_FIELDS and key != "network_ip"
        )
        cidr_match = True
        if "network_cidr" in rule:
            try:
                cidr_match = ipaddress.ip_address(safe_context["network_ip"]) in ipaddress.ip_network(rule["network_cidr"])
            except (KeyError, ValueError) as exc:
                raise GuardPolicyError("invalid network_ip for network_cidr policy") from exc
        if field_match and cidr_match:
            matched.append(rule)
    deny = [rule for rule in matched if rule["effect"] == "deny"]
    if deny:
        ids = tuple(rule["id"] for rule in deny)
        return GuardDecision(False, "deny", "organization policy denied this action", digest, ids)
    approvals = [rule for rule in matched if rule["effect"] == "require_approval"]
    allows = [rule for rule in matched if rule["effect"] == "allow"]
    # Approval is an additional condition, never a substitute for explicit
    # authorization under default-deny.
    if normalized["default_effect"] == "deny" and not allows:
        ids = tuple(rule["id"] for rule in matched)
        return GuardDecision(False, "deny", "organization policy default-deny: no matching allow rule", digest, ids)
    if approvals:
        ids = tuple(rule["id"] for rule in approvals)
        if not approval_gate_configured:
            return GuardDecision(False, "require_approval", "organization policy requires an approval-gated handler before dispatch", digest, ids, True)
        return GuardDecision(True, "require_approval", "dispatch only to the trusted approval-gated execution path", digest, ids, True)
    # An allow rule only satisfies this organization's default-deny policy. It
    # never bypasses the mandatory authorization argument checked above.
    ids = tuple(rule["id"] for rule in matched)
    return GuardDecision(True, "allow", "mandatory gates passed and organization policy permits the action", digest, ids)


__all__ = [
    "POLICY_VERSION", "GuardDecision", "GuardPolicyError", "evaluate_guard_policy",
    "guard_policy_fingerprint", "load_guard_policy", "validate_policy",
]
