"""Dispatch-time validation for durable AUREN agent assignment envelopes.

This protects the controller dispatch boundary against malformed or stale
assignment metadata. It is not a filesystem sandbox: handlers and tool adapters
must enforce the same scope at the operation boundary.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping

from .agent_org_runtime import REGISTRY_PATH, load_agent_org_registry

_SCHEMA = "auren-agent-assignment-v1"
_ASSIGNMENT_KEY = "_auren_agent_assignment"
_MAX_SCOPE_ITEMS = 100
_MAX_SCOPE_LENGTH = 512
_ACTIVE_ASSIGNMENT_SCOPE: ContextVar[tuple[str, ...] | None] = ContextVar(
    "auren_active_agent_assignment_scope", default=None
)


class AgentAssignmentScopeDenied(PermissionError):
    """An assigned task failed dispatch-time identity, digest, or scope checks."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _safe_relative_resource(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_SCOPE_LENGTH:
        raise AgentAssignmentScopeDenied(f"assigned task requires a valid {field}")
    raw = value.strip()
    if "\\" in raw or raw.startswith("/") or any(ord(char) < 32 for char in raw):
        raise AgentAssignmentScopeDenied(f"assigned {field} must be a normalized relative path")
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in raw.split("/")) or path.is_absolute():
        raise AgentAssignmentScopeDenied(f"assigned {field} contains unsafe path components")
    return path.as_posix()


def validate_agent_assignment_for_dispatch(
    payload: Mapping[str, Any],
    *,
    resource: str | None,
    registry_path: str = str(REGISTRY_PATH),
    authorization_verifier: Callable[[Mapping[str, Any]], Any] | None = None,
    expected_action_class: str | None = None,
) -> tuple[str, ...] | None:
    """Validate an optional assignment envelope before any handler dispatch.

    Unassigned legacy tasks retain existing behavior. If the reserved assignment
    field exists, malformed metadata fails closed. Resource scope is mandatory
    for assigned work and must match an exact normalized relative path from
    the assignment. This does not prove host identity or confine
    handler internals; those require host authorization and operation-level checks.
    Assigned work also requires a trusted, persisted handler action class. The
    assignment cannot select or change the action class at dispatch time.
    """
    if not isinstance(payload, Mapping):
        raise AgentAssignmentScopeDenied("task payload must be a mapping")
    if _ASSIGNMENT_KEY not in payload:
        return None
    envelope = payload.get(_ASSIGNMENT_KEY)
    if not isinstance(envelope, Mapping):
        raise AgentAssignmentScopeDenied("agent assignment envelope is malformed")
    required = {
        "schema_version", "assignment_id", "role_id", "manager_id", "action_class",
        "scope", "registry_digest", "assignment_digest", "authorization",
    }
    if set(envelope) != required or envelope.get("schema_version") != _SCHEMA:
        raise AgentAssignmentScopeDenied("agent assignment schema is invalid")
    if envelope.get("authorization") != "host_authorized_before_submission":
        raise AgentAssignmentScopeDenied("agent assignment authorization marker is invalid")
    role_id, manager_id, action_class = (
        envelope.get("role_id"), envelope.get("manager_id"), envelope.get("action_class")
    )
    if any(not isinstance(item, str) or not item.strip() for item in (role_id, manager_id, action_class)):
        raise AgentAssignmentScopeDenied("agent assignment identity fields are invalid")
    scope = envelope.get("scope")
    if not isinstance(scope, list) or not scope or len(scope) > _MAX_SCOPE_ITEMS:
        raise AgentAssignmentScopeDenied("agent assignment scope is missing or exceeds limits")
    clean_scope = [_safe_relative_resource(item, field="scope item") for item in scope]
    if len(set(clean_scope)) != len(clean_scope):
        raise AgentAssignmentScopeDenied("agent assignment scope contains duplicates")
    try:
        registry, registry_digest = load_agent_org_registry(registry_path)
    except Exception as exc:
        raise AgentAssignmentScopeDenied("agent organization registry cannot be verified") from exc
    if envelope.get("registry_digest") != registry_digest:
        raise AgentAssignmentScopeDenied("agent assignment registry digest is stale")
    roles = {item["id"]: item for item in registry["roles"]}
    role = roles.get(role_id)
    if role is None or role.get("reports_to") != manager_id:
        raise AgentAssignmentScopeDenied("agent assignment role or manager no longer matches registry")
    if action_class not in role.get("allowed_action_classes", []):
        raise AgentAssignmentScopeDenied("agent assignment action class is no longer permitted")
    if not isinstance(expected_action_class, str) or not expected_action_class:
        raise AgentAssignmentScopeDenied("assigned work requires a trusted handler action class")
    if action_class != expected_action_class:
        raise AgentAssignmentScopeDenied("assignment action class does not match the registered handler")
    body = {
        "schema_version": _SCHEMA,
        "role_id": role_id,
        "manager_id": manager_id,
        "action_class": action_class,
        "scope": clean_scope,
        "registry_digest": registry_digest,
    }
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    if envelope.get("assignment_digest") != digest:
        raise AgentAssignmentScopeDenied("agent assignment digest does not match its contents")
    if envelope.get("assignment_id") != "auren-assignment-" + digest:
        raise AgentAssignmentScopeDenied("agent assignment ID does not match its digest")
    if not callable(authorization_verifier):
        raise AgentAssignmentScopeDenied("host-owned assignment authorization verifier is required")
    verification_request = MappingProxyType({
        "schema_version": _SCHEMA,
        "assignment_id": envelope["assignment_id"],
        "role_id": role_id,
        "manager_id": manager_id,
        "action_class": action_class,
        "scope": tuple(clean_scope),
        "registry_digest": registry_digest,
        "assignment_digest": digest,
    })
    try:
        authorized = authorization_verifier(verification_request)
    except Exception as exc:
        raise AgentAssignmentScopeDenied("host-owned assignment authorization verification failed") from exc
    if authorized is not True:
        raise AgentAssignmentScopeDenied("host-owned assignment authorization verifier denied the assignment")
    target = _safe_relative_resource(resource, field="resource selector")
    allowed = target in clean_scope
    if not allowed:
        raise AgentAssignmentScopeDenied("task resource is outside the assigned scope")
    return tuple(clean_scope)


@contextmanager
def agent_assignment_scope(scope: tuple[str, ...] | None) -> Iterator[None]:
    """Bind verified exact-path scope to synchronous adapter calls in this context."""
    token: Token[tuple[str, ...] | None] = _ACTIVE_ASSIGNMENT_SCOPE.set(scope)
    try:
        yield
    finally:
        _ACTIVE_ASSIGNMENT_SCOPE.reset(token)


def enforce_agent_resource_scope(relative_path: Any) -> None:
    """Deny guarded filesystem access outside the current verified assignment."""
    active = _ACTIVE_ASSIGNMENT_SCOPE.get()
    if active is None:
        return
    target = _safe_relative_resource(relative_path, field="filesystem resource")
    if target not in active:
        raise AgentAssignmentScopeDenied("filesystem resource is outside the assigned scope")


__all__ = [
    "AgentAssignmentScopeDenied", "agent_assignment_scope",
    "enforce_agent_resource_scope", "validate_agent_assignment_for_dispatch",
]
