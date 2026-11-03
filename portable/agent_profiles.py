"""Versioned, non-authoritative agent profiles and reusable team templates.

Profiles describe role-bound preferences. The organization registry and canonical
ExecutionController remain the sole source of authority and dispatch policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .agent_org_runtime import load_agent_org_registry

PROFILE_REGISTRY_VERSION = "auren-agent-profiles-v1"
PROFILE_SCHEMA_VERSION = "auren-agent-profile-v1"
TEAM_TEMPLATE_SCHEMA_VERSION = "auren-agent-team-template-v1"
DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[1] / "config" / "agent_profiles.v1.json"
DEFAULT_ORGANIZATION_PATH = Path(__file__).resolve().parents[1] / "config" / "agent_org.v1.json"
_MAX_REGISTRY_BYTES = 256 * 1024
_MAX_PROFILES = 128
_MAX_TEMPLATES = 64
_MAX_TEAM_MEMBERS = 32
_MAX_TEXT = 256
_APPROVAL_POLICIES = frozenset({
    "inherit_role",
    "manager_before_external_effect",
    "independent_review_before_release",
})
_WORKSPACE_MODES = frozenset({"task_scoped", "read_only", "isolated_task"})


class AgentProfileError(ValueError):
    """Profile or team template is invalid, stale, or exceeds canonical role policy."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AgentProfileError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: Any, field: str, *, pattern: str | None = None) -> str:
    if (
        not isinstance(value, str) or not value.strip()
        or len(value.strip()) > _MAX_TEXT or not value.isprintable()
    ):
        raise AgentProfileError(f"{field} must be non-empty printable text of at most {_MAX_TEXT} characters")
    value = value.strip()
    if pattern is not None and re.fullmatch(pattern, value) is None:
        raise AgentProfileError(f"{field} has an invalid format")
    return value


def _string_list(value: Any, field: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (required and not value) or len(value) > 64:
        raise AgentProfileError(f"{field} must be a {'non-empty ' if required else ''}list of at most 64 strings")
    result = tuple(_text(item, field) for item in value)
    if len(set(result)) != len(result):
        raise AgentProfileError(f"{field} contains duplicates")
    return result


def _positive_int(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise AgentProfileError(f"{field} must be an integer between 1 and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class AgentProfile:
    profile_id: str
    revision: int
    role_id: str
    manager_role_id: str
    action_classes: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    provider_preferences: tuple[str, ...]
    tool_preferences: tuple[str, ...]
    workspace_mode: str
    memory_namespace: str
    budget_defaults: Mapping[str, int]
    deadline_seconds: int
    approval_policy: str
    digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "revision": self.revision,
            "role_id": self.role_id,
            "manager_role_id": self.manager_role_id,
            "action_classes": list(self.action_classes),
            "required_capabilities": list(self.required_capabilities),
            "provider_preferences": list(self.provider_preferences),
            "tool_preferences": list(self.tool_preferences),
            "workspace_policy": {
                "mode": self.workspace_mode,
                "scope_source": "canonical_assignment",
            },
            "memory_namespace": self.memory_namespace,
            "budget_defaults": dict(self.budget_defaults),
            "deadline_seconds": self.deadline_seconds,
            "approval_policy": self.approval_policy,
            "digest": self.digest,
            "authority_note": "routing hints only; no role, tool, action, approval, or workspace authority is granted",
        }


def _load_json(path: str | Path, *, label: str) -> dict[str, Any]:
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise AgentProfileError(f"{label} cannot be read") from exc
    if not raw or len(raw) > _MAX_REGISTRY_BYTES:
        raise AgentProfileError(f"{label} is empty or exceeds the {_MAX_REGISTRY_BYTES}-byte limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentProfileError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise AgentProfileError(f"{label} root must be an object")
    return value


def _parse_profile(raw: Any, roles: Mapping[str, Mapping[str, Any]]) -> AgentProfile:
    if not isinstance(raw, dict):
        raise AgentProfileError("profile entry must be an object")
    required = {
        "profile_id", "revision", "role_id", "manager_role_id", "action_classes",
        "required_capabilities", "provider_preferences", "tool_preferences",
        "workspace_policy", "memory_namespace", "budget_defaults",
        "deadline_seconds", "approval_policy",
    }
    if set(raw) != required:
        raise AgentProfileError("profile fields do not match the versioned schema")
    profile_id = _text(raw["profile_id"], "profile_id", pattern=r"[a-z][a-z0-9_]{1,63}")
    revision = _positive_int(raw["revision"], "revision", 1000000)
    role_id = _text(raw["role_id"], "role_id")
    manager_role_id = _text(raw["manager_role_id"], "manager_role_id")
    role = roles.get(role_id)
    if role is None:
        raise AgentProfileError(f"unknown canonical role: {role_id}")
    if role.get("reports_to") != manager_role_id:
        raise AgentProfileError("profile manager_role_id does not match canonical role hierarchy")
    actions = _string_list(raw["action_classes"], "action_classes", required=True)
    canonical_actions = role.get("allowed_action_classes")
    if not isinstance(canonical_actions, list) or not set(actions).issubset(set(canonical_actions)):
        raise AgentProfileError("profile action_classes exceeds canonical role authority")
    required_capabilities = _string_list(raw["required_capabilities"], "required_capabilities", required=True)
    providers = _string_list(raw["provider_preferences"], "provider_preferences")
    tools = _string_list(raw["tool_preferences"], "tool_preferences")
    workspace = raw["workspace_policy"]
    if (
        not isinstance(workspace, dict)
        or set(workspace) != {"mode", "scope_source"}
        or workspace.get("mode") not in _WORKSPACE_MODES
        or workspace.get("scope_source") != "canonical_assignment"
    ):
        raise AgentProfileError("workspace_policy must inherit canonical task assignment scope")
    memory_namespace = _text(raw["memory_namespace"], "memory_namespace")
    if memory_namespace != f"agent-profile:{profile_id}":
        raise AgentProfileError("memory_namespace must be isolated to the profile identity")
    budget = raw["budget_defaults"]
    if not isinstance(budget, dict) or set(budget) != {"max_tokens", "max_tool_calls", "max_wall_seconds"}:
        raise AgentProfileError("budget_defaults fields are invalid")
    budget_defaults = {
        "max_tokens": _positive_int(budget["max_tokens"], "max_tokens", 2000000),
        "max_tool_calls": _positive_int(budget["max_tool_calls"], "max_tool_calls", 10000),
        "max_wall_seconds": _positive_int(budget["max_wall_seconds"], "max_wall_seconds", 86400),
    }
    deadline = _positive_int(raw["deadline_seconds"], "deadline_seconds", 86400)
    approval_policy = _text(raw["approval_policy"], "approval_policy")
    if approval_policy not in _APPROVAL_POLICIES:
        raise AgentProfileError("approval_policy is not supported")
    body = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": profile_id,
        "revision": revision,
        "role_id": role_id,
        "manager_role_id": manager_role_id,
        "action_classes": list(actions),
        "required_capabilities": list(required_capabilities),
        "provider_preferences": list(providers),
        "tool_preferences": list(tools),
        "workspace_policy": {"mode": workspace["mode"], "scope_source": "canonical_assignment"},
        "memory_namespace": memory_namespace,
        "budget_defaults": budget_defaults,
        "deadline_seconds": deadline,
        "approval_policy": approval_policy,
    }
    return AgentProfile(
        profile_id=profile_id, revision=revision, role_id=role_id,
        manager_role_id=manager_role_id, action_classes=actions,
        required_capabilities=required_capabilities, provider_preferences=providers,
        tool_preferences=tools, workspace_mode=workspace["mode"],
        memory_namespace=memory_namespace, budget_defaults=budget_defaults,
        deadline_seconds=deadline, approval_policy=approval_policy,
        digest=_digest(body),
    )


def _validate_templates(raw_templates: Any, profiles: Mapping[tuple[str, int], AgentProfile]) -> dict[tuple[str, int], dict[str, Any]]:
    if not isinstance(raw_templates, list) or len(raw_templates) > _MAX_TEMPLATES:
        raise AgentProfileError("team_templates must be a list within the configured limit")
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in raw_templates:
        if not isinstance(raw, dict) or set(raw) != {"template_id", "revision", "description", "members"}:
            raise AgentProfileError("team template fields do not match the versioned schema")
        template_id = _text(raw["template_id"], "template_id", pattern=r"[a-z][a-z0-9_]{1,63}")
        revision = _positive_int(raw["revision"], "template revision", 1000000)
        description = _text(raw["description"], "template description")
        members_raw = raw["members"]
        if not isinstance(members_raw, list) or not members_raw or len(members_raw) > _MAX_TEAM_MEMBERS:
            raise AgentProfileError("team template members are missing or exceed limits")
        members: list[dict[str, Any]] = []
        member_ids: set[str] = set()
        for member in members_raw:
            if not isinstance(member, dict) or set(member) != {"member_id", "profile_id", "profile_revision", "depends_on"}:
                raise AgentProfileError("team member fields do not match the versioned schema")
            member_id = _text(member["member_id"], "member_id", pattern=r"[a-z][a-z0-9_]{1,63}")
            if member_id in member_ids:
                raise AgentProfileError("team template member IDs must be unique")
            member_ids.add(member_id)
            profile_id = _text(member["profile_id"], "profile_id")
            profile_revision = _positive_int(member["profile_revision"], "profile_revision", 1000000)
            if (profile_id, profile_revision) not in profiles:
                raise AgentProfileError(f"team template references unknown profile: {profile_id}@{profile_revision}")
            dependencies = _string_list(member["depends_on"], "depends_on")
            members.append({
                "member_id": member_id, "profile_id": profile_id,
                "profile_revision": profile_revision, "depends_on": list(dependencies),
            })
        for member in members:
            unknown = set(member["depends_on"]) - member_ids
            if unknown or member["member_id"] in member["depends_on"]:
                raise AgentProfileError("team template dependency references an unknown or self member")
        by_id = {member["member_id"]: member for member in members}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(member_id: str) -> None:
            if member_id in visiting:
                raise AgentProfileError("team template dependency cycle detected")
            if member_id in visited:
                return
            visiting.add(member_id)
            for dependency in by_id[member_id]["depends_on"]:
                visit(dependency)
            visiting.remove(member_id)
            visited.add(member_id)

        for member_id in by_id:
            visit(member_id)
        key = (template_id, revision)
        if key in result:
            raise AgentProfileError(f"duplicate team template revision: {template_id}@{revision}")
        body = {
            "schema_version": TEAM_TEMPLATE_SCHEMA_VERSION,
            "template_id": template_id, "revision": revision,
            "description": description, "members": members,
        }
        result[key] = {**body, "digest": _digest(body)}
    return result


def load_agent_profile_registry(
    profile_path: str | Path = DEFAULT_PROFILE_PATH,
    *,
    organization_registry_path: str | Path = DEFAULT_ORGANIZATION_PATH,
) -> tuple[dict[str, Any], str]:
    """Load and validate profile/template data against canonical organization roles."""
    raw = _load_json(profile_path, label="agent profile registry")
    if set(raw) != {"schema_version", "profiles", "team_templates"} or raw.get("schema_version") != PROFILE_REGISTRY_VERSION:
        raise AgentProfileError("unsupported or malformed agent profile registry schema")
    try:
        org, _org_digest = load_agent_org_registry(organization_registry_path)
    except Exception as exc:
        raise AgentProfileError("canonical organization registry is invalid") from exc
    roles = {role["id"]: role for role in org["roles"]}
    profiles_raw = raw["profiles"]
    if not isinstance(profiles_raw, list) or len(profiles_raw) > _MAX_PROFILES:
        raise AgentProfileError("profiles must be a list within the configured limit")
    profiles: dict[tuple[str, int], AgentProfile] = {}
    for item in profiles_raw:
        profile = _parse_profile(item, roles)
        key = (profile.profile_id, profile.revision)
        if key in profiles:
            raise AgentProfileError(f"duplicate profile revision: {profile.profile_id}@{profile.revision}")
        profiles[key] = profile
    templates = _validate_templates(raw["team_templates"], profiles)
    profile_items = [profiles[key].as_dict() for key in sorted(profiles)]
    template_items = [templates[key] for key in sorted(templates)]
    normalized = {
        "schema_version": PROFILE_REGISTRY_VERSION,
        "profiles": profile_items,
        "team_templates": template_items,
    }
    return normalized, _digest(normalized)


def resolve_agent_profile(
    profile_id: str,
    *,
    revision: int,
    profile_path: str | Path = DEFAULT_PROFILE_PATH,
    organization_registry_path: str | Path = DEFAULT_ORGANIZATION_PATH,
) -> AgentProfile:
    normalized, _ = load_agent_profile_registry(
        profile_path, organization_registry_path=organization_registry_path,
    )
    for item in normalized["profiles"]:
        if item["profile_id"] == profile_id and item["revision"] == revision:
            return AgentProfile(
                profile_id=item["profile_id"], revision=item["revision"],
                role_id=item["role_id"], manager_role_id=item["manager_role_id"],
                action_classes=tuple(item["action_classes"]),
                required_capabilities=tuple(item["required_capabilities"]),
                provider_preferences=tuple(item["provider_preferences"]),
                tool_preferences=tuple(item["tool_preferences"]),
                workspace_mode=item["workspace_policy"]["mode"],
                memory_namespace=item["memory_namespace"],
                budget_defaults=dict(item["budget_defaults"]),
                deadline_seconds=item["deadline_seconds"],
                approval_policy=item["approval_policy"], digest=item["digest"],
            )
    raise AgentProfileError(f"unknown profile revision: {profile_id}@{revision}")


def resolve_team_template(
    template_id: str,
    *,
    revision: int,
    profile_path: str | Path = DEFAULT_PROFILE_PATH,
    organization_registry_path: str | Path = DEFAULT_ORGANIZATION_PATH,
) -> dict[str, Any]:
    normalized, _ = load_agent_profile_registry(
        profile_path, organization_registry_path=organization_registry_path,
    )
    for item in normalized["team_templates"]:
        if item["template_id"] == template_id and item["revision"] == revision:
            return dict(item)
    raise AgentProfileError(f"unknown team template revision: {template_id}@{revision}")
