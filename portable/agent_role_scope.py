"""Fail-closed role and delegated-scope checks for canonical execution dispatch.

The registry describes role boundaries; it does not grant authority. A host-supplied
authorizer must validate the task-bound delegation and approval evidence at dispatch.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

ROLE_SCOPE_KEY = "_auren_role_scope"
ROLE_SCOPE_SCHEMA_VERSION = "auren-agent-role-scope-v1"
DEFAULT_ROLE_REGISTRY = Path(__file__).resolve().parents[1] / "config" / "agent_org.v1.json"
_ROLE_SCOPE_FIELDS = {
    "schema_version", "role_id", "manager_role_id", "action_class",
    "resource_scope", "budget", "deadline_at", "policy_digest",
    "delegation_evidence_ref",
}
_OPTIONAL_SCOPE_FIELDS = {"approval_evidence_ref"}
_HIGH_RISK_CLASSES = frozenset({"consequential", "approval_gated"})


class RoleScopeError(ValueError):
    """Role, delegation, policy, resource, or budget validation failed."""


def _canonical_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RoleScopeError("role-scope data is not finite JSON data") from exc
    return hashlib.sha256(encoded).hexdigest()


def _registry_path(path: str | Path | None) -> Path:
    return Path(path).expanduser().resolve() if path is not None else DEFAULT_ROLE_REGISTRY


def load_role_registry(path: str | Path | None = None) -> tuple[dict[str, Any], str]:
    """Load the current role registry and return it with its canonical SHA-256 digest."""
    selected = _registry_path(path)
    try:
        registry = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoleScopeError("trusted role registry is unavailable or invalid") from exc
    if not isinstance(registry, dict) or registry.get("schema_version") != "auren-ai-engineering-org-v1":
        raise RoleScopeError("unsupported trusted role registry schema")
    roles = registry.get("roles")
    external = registry.get("external_entities")
    if not isinstance(roles, list) or not isinstance(external, list):
        raise RoleScopeError("trusted role registry is missing role or external-entity records")
    by_id: dict[str, Mapping[str, Any]] = {}
    for role in roles:
        if not isinstance(role, Mapping):
            raise RoleScopeError("trusted role registry contains a malformed role")
        role_id = role.get("id")
        if not isinstance(role_id, str) or not role_id.strip() or role_id in by_id:
            raise RoleScopeError("trusted role registry has missing or duplicate role IDs")
        if not isinstance(role.get("allowed_action_classes"), list) or not isinstance(role.get("prohibited_actions"), list):
            raise RoleScopeError("trusted role registry has malformed action boundaries")
        by_id[role_id] = role
    external_ids = {
        item.get("id") for item in external
        if isinstance(item, Mapping) and isinstance(item.get("id"), str) and item.get("id")
    }
    known = set(by_id) | external_ids
    for role_id, role in by_id.items():
        parent = role.get("reports_to")
        if not isinstance(parent, str) or parent not in known or parent == role_id:
            raise RoleScopeError("trusted role registry has an invalid reporting parent")
    for role_id in by_id:
        seen: set[str] = set()
        current = role_id
        while current in by_id:
            if current in seen:
                raise RoleScopeError("trusted role registry contains a reporting cycle")
            seen.add(current)
            current = str(by_id[current].get("reports_to", ""))
    return registry, _canonical_digest(registry)


def role_policy_digest(path: str | Path | None = None) -> str:
    """Return the digest callers must bind into a role-scope intent."""
    return load_role_registry(path)[1]


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RoleScopeError(f"{field} must be an explicit timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RoleScopeError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RoleScopeError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _resource(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoleScopeError(f"{field} must be a non-empty resource string")
    value = value.strip()
    if value.startswith(("/", "\\")) or "\\" in value:
        raise RoleScopeError(f"{field} must be a relative resource selector")
    parts = value.split("/")
    if any(part in {".", ".."} for part in parts):
        raise RoleScopeError(f"{field} cannot contain traversal segments")
    return value


def _task_resources(payload: Mapping[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    for key in ("path", "resource", "resource_id"):
        if key in payload and payload[key] is not None:
            found.append(_resource(payload[key], f"payload.{key}"))
    for key in ("paths", "resources"):
        if key in payload and payload[key] is not None:
            values = payload[key]
            if isinstance(values, str) or not isinstance(values, (list, tuple)) or not values:
                raise RoleScopeError(f"payload.{key} must be a non-empty list of resource selectors")
            found.extend(_resource(item, f"payload.{key}") for item in values)
    return tuple(dict.fromkeys(found))


def _inside_scope(resource: str, allowed: str) -> bool:
    normalized = allowed.rstrip("/")
    return resource == normalized or resource.startswith(normalized + "/")


def validate_role_scope(
    scope: Mapping[str, Any],
    *,
    registry_path: str | Path | None = None,
    execution_class: str,
    delivery_action: str | None = None,
    payload: Mapping[str, Any] | None = None,
    task_id: str | None = None,
    task_deadline_at: str | None = None,
    task_created_at: str | None = None,
    task_attempts: int | None = None,
    authorizer: Callable[[str, Mapping[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Validate a supervisor-owned scope; call the trusted authorizer on every dispatch."""
    if not isinstance(scope, Mapping):
        raise RoleScopeError("role scope must be a mapping")
    keys = set(scope)
    if not _ROLE_SCOPE_FIELDS.issubset(keys) or keys - _ROLE_SCOPE_FIELDS - _OPTIONAL_SCOPE_FIELDS:
        raise RoleScopeError("role scope has missing or unsupported fields")
    if scope.get("schema_version") != ROLE_SCOPE_SCHEMA_VERSION:
        raise RoleScopeError("unsupported role-scope schema")
    registry, expected_policy_digest = load_role_registry(registry_path)
    if scope.get("policy_digest") != expected_policy_digest:
        raise RoleScopeError("role-scope policy digest is stale or mismatched")
    role_id = scope.get("role_id")
    manager_id = scope.get("manager_role_id")
    action_class = scope.get("action_class")
    if not all(isinstance(value, str) and value.strip() for value in (role_id, manager_id, action_class)):
        raise RoleScopeError("role ID, manager ID, and action class are required")
    roles = {role["id"]: role for role in registry["roles"]}
    role = roles.get(role_id)
    if role is None:
        raise RoleScopeError("unknown role ID")
    if manager_id != role.get("reports_to"):
        raise RoleScopeError("manager delegation does not match the role reporting boundary")
    if action_class not in role["allowed_action_classes"] or action_class in role["prohibited_actions"]:
        raise RoleScopeError("role is not permitted to perform the requested action class")
    if delivery_action is not None:
        if delivery_action not in role["allowed_action_classes"] or action_class != delivery_action:
            raise RoleScopeError("role scope does not authorize the handler's delivery action")
        if execution_class == "read_only":
            raise RoleScopeError("repository delivery actions cannot run through a read-only handler")
    if action_class == "edit_assigned_paths" and execution_class == "read_only":
        raise RoleScopeError("path-edit actions cannot run through a read-only handler")
    if role_id == "EVAL-01" and execution_class in _HIGH_RISK_CLASSES:
        raise RoleScopeError("independent evaluator identity cannot execute consequential actions")

    raw_scope = scope.get("resource_scope")
    if isinstance(raw_scope, str) or not isinstance(raw_scope, (list, tuple)) or not raw_scope or len(raw_scope) > 64:
        raise RoleScopeError("resource_scope must contain 1 to 64 explicit resource selectors")
    resource_scope = tuple(dict.fromkeys(_resource(item, "resource_scope") for item in raw_scope))
    resources = _task_resources(payload or {})
    if action_class == "edit_assigned_paths" and not resources:
        raise RoleScopeError("path-edit actions require explicit task resource selectors")
    for resource in resources:
        if not any(_inside_scope(resource, allowed) for allowed in resource_scope):
            raise RoleScopeError("task resource is outside the delegated resource scope")

    budget = scope.get("budget")
    if not isinstance(budget, Mapping) or set(budget) != {"max_attempts", "max_duration_seconds"}:
        raise RoleScopeError("role budget must define only max_attempts and max_duration_seconds")
    max_attempts = budget.get("max_attempts")
    max_duration = budget.get("max_duration_seconds")
    if type(max_attempts) is not int or not 1 <= max_attempts <= 12:
        raise RoleScopeError("max_attempts must be an integer from 1 to 12")
    if (isinstance(max_duration, bool) or not isinstance(max_duration, (int, float))
            or not math.isfinite(max_duration) or not 1 <= max_duration <= 86400):
        raise RoleScopeError("max_duration_seconds must be finite and between 1 and 86400")
    scope_deadline = _timestamp(scope.get("deadline_at"), "deadline_at")
    now = datetime.now(timezone.utc)
    if scope_deadline <= now:
        raise RoleScopeError("role delegation has expired")
    if task_attempts is not None and (type(task_attempts) is not int or task_attempts > max_attempts):
        raise RoleScopeError("role-scope attempt budget is exhausted")

    delegation_ref = scope.get("delegation_evidence_ref")
    if not isinstance(delegation_ref, str) or not delegation_ref.strip():
        raise RoleScopeError("trusted delegation evidence reference is required")
    approval_ref = scope.get("approval_evidence_ref")
    if approval_ref is not None and (not isinstance(approval_ref, str) or not approval_ref.strip()):
        raise RoleScopeError("approval_evidence_ref must be a non-empty reference when present")
    if execution_class in _HIGH_RISK_CLASSES and not approval_ref:
        raise RoleScopeError("consequential actions require separate approval evidence")

    normalized: dict[str, Any] = {
        "schema_version": ROLE_SCOPE_SCHEMA_VERSION,
        "role_id": role_id,
        "manager_role_id": manager_id,
        "action_class": action_class,
        "resource_scope": list(resource_scope),
        "budget": {"max_attempts": max_attempts, "max_duration_seconds": float(max_duration)},
        "deadline_at": scope_deadline.isoformat(),
        "policy_digest": expected_policy_digest,
        "delegation_evidence_ref": delegation_ref.strip(),
    }
    if approval_ref is not None:
        normalized["approval_evidence_ref"] = approval_ref.strip()

    if task_id is not None:
        if not isinstance(task_id, str) or not task_id.strip():
            raise RoleScopeError("durable task ID is required for dispatch authorization")
        if task_deadline_at is None or task_created_at is None:
            raise RoleScopeError("durable task deadline/creation time is missing")
        task_deadline = _timestamp(task_deadline_at, "task_deadline_at")
        created_at = _timestamp(task_created_at, "task_created_at")
        if task_deadline > scope_deadline:
            raise RoleScopeError("durable task deadline exceeds the delegated deadline")
        if (task_deadline - created_at).total_seconds() > float(max_duration) + 1.0:
            raise RoleScopeError("durable task deadline exceeds the delegated duration budget")
        if not callable(authorizer):
            raise RoleScopeError("trusted role-scope authorizer is unavailable; dispatch denied")
        try:
            authorized = authorizer(task_id, normalized)
        except Exception as exc:
            raise RoleScopeError("trusted role-scope authorizer failed; dispatch denied") from exc
        if authorized is not True:
            raise RoleScopeError("trusted role-scope authorizer denied dispatch")
    elif not callable(authorizer):
        raise RoleScopeError("trusted role-scope authorizer is unavailable; role-scoped work is disabled")

    return normalized


def effective_role_deadline(
    scope: Mapping[str, Any],
    requested_deadline_seconds: float | None = None,
) -> str:
    """Compute the task deadline as the tighter of the delegated expiry and duration budget."""
    budget = scope.get("budget")
    if not isinstance(budget, Mapping):
        raise RoleScopeError("role budget is missing")
    maximum = float(budget.get("max_duration_seconds", 0))
    if requested_deadline_seconds is None:
        duration = maximum
    else:
        if (isinstance(requested_deadline_seconds, bool)
                or not isinstance(requested_deadline_seconds, (int, float))
                or not math.isfinite(requested_deadline_seconds)
                or requested_deadline_seconds <= 0
                or requested_deadline_seconds > maximum):
            raise RoleScopeError("requested deadline exceeds the delegated duration budget")
        duration = float(requested_deadline_seconds)
    now = datetime.now(timezone.utc)
    delegated_expiry = _timestamp(scope.get("deadline_at"), "deadline_at")
    deadline = min(now + timedelta(seconds=duration), delegated_expiry)
    if deadline <= now:
        raise RoleScopeError("role delegation has expired")
    return deadline.isoformat()


__all__ = [
    "ROLE_SCOPE_KEY", "ROLE_SCOPE_SCHEMA_VERSION", "RoleScopeError",
    "effective_role_deadline", "load_role_registry", "role_policy_digest",
    "validate_role_scope",
]
