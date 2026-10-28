"""Host-authorized role assignment for the AUREN engineering organization.

The registry describes roles; this module adds a fail-closed assignment boundary.
It does not grant tools or bypass ExecutionController policy. A host-owned
authorizer must authenticate the assigning manager and approve the exact
canonical assignment before the task is durably submitted.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from .organization_guard import POLICY_VERSION, guard_policy_fingerprint, load_guard_policy


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "agent_org.v1.json"
_SCHEMA = "auren-agent-assignment-v2"
_MAX_REGISTRY_BYTES = 256 * 1024
_MAX_SCOPE_ITEMS = 100
_MAX_SCOPE_LENGTH = 512


class AgentAssignmentDenied(PermissionError):
    """Assignment failed role, scope, or host authorization checks."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate organization registry field: {key}")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class AuthorizedAgentAssignment:
    assignment_id: str
    role_id: str
    manager_id: str
    action_class: str
    scope: tuple[str, ...]
    registry_digest: str
    policy_version: str
    policy_digest: str
    assignment_digest: str

    def as_payload(self) -> dict[str, Any]:
        """Return the immutable, non-secret assignment envelope stored with the task."""
        return {
            "schema_version": _SCHEMA,
            "assignment_id": self.assignment_id,
            "role_id": self.role_id,
            "manager_id": self.manager_id,
            "action_class": self.action_class,
            "scope": list(self.scope),
            "registry_digest": self.registry_digest,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "assignment_digest": self.assignment_digest,
            "authorization": "host_authorized_before_submission",
        }


def load_agent_org_registry(path: str | Path = REGISTRY_PATH) -> tuple[dict[str, Any], str]:
    target = Path(path)
    raw = target.read_bytes()
    if not raw or len(raw) > _MAX_REGISTRY_BYTES:
        raise ValueError("organization registry is empty or exceeds the size limit")
    registry = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    if not isinstance(registry, dict) or registry.get("schema_version") != "auren-ai-engineering-org-v1":
        raise ValueError("unsupported organization registry schema")
    roles = registry.get("roles")
    external = registry.get("external_entities")
    if not isinstance(roles, list) or not isinstance(external, list):
        raise ValueError("organization registry requires roles and external_entities lists")
    role_map: dict[str, dict[str, Any]] = {}
    for role in roles:
        if not isinstance(role, dict) or not isinstance(role.get("id"), str) or not role["id"].strip():
            raise ValueError("organization role has an invalid id")
        if role["id"] in role_map:
            raise ValueError("organization role IDs must be unique")
        required = ("title", "reports_to", "allowed_action_classes", "approval_boundary")
        if any(not role.get(field) for field in required):
            raise ValueError(f"organization role {role['id']} is incomplete")
        if not isinstance(role["allowed_action_classes"], list) or any(
            not isinstance(item, str) or not item.strip() for item in role["allowed_action_classes"]
        ):
            raise ValueError(f"organization role {role['id']} has invalid action classes")
        role_map[role["id"]] = role
    external_ids = {
        item.get("id") for item in external
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
    }
    if len(external_ids) != len(external):
        raise ValueError("organization external entity IDs must be present and unique")
    known = set(role_map) | external_ids
    for role in role_map.values():
        if role["reports_to"] not in known or role["reports_to"] == role["id"]:
            raise ValueError(f"organization role {role['id']} has an invalid reporting parent")
        seen: set[str] = set()
        current = role["id"]
        while current in role_map:
            if current in seen:
                raise ValueError(f"organization reporting cycle at {current}")
            seen.add(current)
            current = role_map[current]["reports_to"]
    normalized = dict(registry)
    return normalized, hashlib.sha256(_canonical(normalized)).hexdigest()


def authorize_agent_assignment(
    *,
    role_id: str,
    manager_id: str,
    action_class: str,
    scope: Sequence[str],
    authorizer: Callable[[Mapping[str, Any]], Any],
    registry_path: str | Path = REGISTRY_PATH,
    policy_path: str | Path | None = None,
) -> AuthorizedAgentAssignment:
    """Validate the role hierarchy and require host authorization of exact inputs.

    The authorizer must authenticate the manager using host-owned identity and
    policy. Truthy values are deliberately insufficient: only literal True passes.
    """
    if not callable(authorizer):
        raise AgentAssignmentDenied("host assignment authorizer is required")
    for value, field in ((role_id, "role_id"), (manager_id, "manager_id"),
                         (action_class, "action_class")):
        if not isinstance(value, str) or not value.strip() or not value.isprintable():
            raise ValueError(f"{field} must be non-empty printable text")
    if not isinstance(scope, Sequence) or isinstance(scope, (str, bytes)) or not scope:
        raise ValueError("assignment scope must be a non-empty sequence")
    if len(scope) > _MAX_SCOPE_ITEMS:
        raise ValueError("assignment scope exceeds the item limit")
    clean_scope: list[str] = []
    for item in scope:
        if not isinstance(item, str) or not item.strip() or len(item) > _MAX_SCOPE_LENGTH or not item.isprintable():
            raise ValueError("assignment scope contains invalid text")
        clean_scope.append(item.strip())
    if len(set(clean_scope)) != len(clean_scope):
        raise ValueError("assignment scope items must be unique")

    registry, registry_digest = load_agent_org_registry(registry_path)
    try:
        configured_policy = load_guard_policy(policy_path) if policy_path is not None else None
        policy_version = POLICY_VERSION
        policy_digest = guard_policy_fingerprint(configured_policy)
    except Exception as exc:
        raise AgentAssignmentDenied("configured Guard policy cannot be verified") from exc
    role = next((item for item in registry["roles"] if item["id"] == role_id.strip()), None)
    if role is None:
        raise AgentAssignmentDenied("unknown agent role")
    if role["reports_to"] != manager_id.strip():
        raise AgentAssignmentDenied("assigning manager is not the role's configured reporting parent")
    if action_class.strip() not in role["allowed_action_classes"]:
        raise AgentAssignmentDenied("action class is not permitted by the role contract")

    body = {
        "schema_version": _SCHEMA,
        "role_id": role_id.strip(),
        "manager_id": manager_id.strip(),
        "action_class": action_class.strip(),
        "scope": clean_scope,
        "registry_digest": registry_digest,
        "policy_version": policy_version,
        "policy_digest": policy_digest,
    }
    assignment_digest = hashlib.sha256(_canonical(body)).hexdigest()
    request = MappingProxyType({**body, "scope": tuple(clean_scope), "assignment_digest": assignment_digest})
    try:
        approved = authorizer(request)
    except Exception as exc:
        raise AgentAssignmentDenied("host assignment authorization failed closed") from exc
    if approved is not True:
        raise AgentAssignmentDenied("host assignment authorizer must return literal True")
    return AuthorizedAgentAssignment(
        assignment_id="auren-assignment-" + assignment_digest,
        role_id=body["role_id"],
        manager_id=body["manager_id"],
        action_class=body["action_class"],
        scope=tuple(clean_scope),
        registry_digest=registry_digest,
        policy_version=policy_version,
        policy_digest=policy_digest,
        assignment_digest=assignment_digest,
    )


def submit_agent_task(
    controller: Any,
    handler: str,
    payload: Mapping[str, Any],
    *,
    role_id: str,
    manager_id: str,
    action_class: str,
    scope: Sequence[str],
    authorizer: Callable[[Mapping[str, Any]], Any],
    goal: str | None = None,
    work_key: str | None = None,
    deadline_seconds: float | None = None,
    registry_path: str | Path = REGISTRY_PATH,
) -> Any:
    """Authorize a manager-to-agent assignment, then persist it with the task.

    This is an assignment preflight, not a replacement for controller execution
    policy. The controller remains responsible for tool authorization, approvals,
    cancellation, deadlines, idempotency, and evidence-required completion.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("task payload must be a mapping")
    if "_auren_agent_assignment" in payload:
        raise ValueError("payload key '_auren_agent_assignment' is reserved")
    controller_root = getattr(controller, "root", None)
    policy_path = (
        Path(controller_root) / ".auren" / "guard_policy.json"
        if isinstance(controller_root, (str, Path)) else None
    )
    assignment = authorize_agent_assignment(
        role_id=role_id, manager_id=manager_id, action_class=action_class,
        scope=scope, authorizer=authorizer, registry_path=registry_path,
        policy_path=policy_path,
    )
    durable_payload = dict(payload)
    durable_payload["_auren_agent_assignment"] = assignment.as_payload()
    return controller.submit(
        handler, durable_payload, goal=goal, work_key=work_key,
        deadline_seconds=deadline_seconds,
    )
