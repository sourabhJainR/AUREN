"""Versioned, secret-free provider profiles for AUREN graph-team roles.

Profiles choose provider/model routing only. They do not grant tools, action
authority, approval rights, workspace access, or permission to send private data
to a remote provider.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from .crew_adapters import (
    ApprovedToolRegistry,
    SOFTWARE_TEAM_PERSONA_IDS,
    SPECIALIST_CREW_IDS,
)
from .remote_model_providers import OpenAICompatibleProvider, PROVIDER_SPECS

PROFILE_SCHEMA_VERSION = "auren-provider-role-profile-v1"
DEFAULT_PROFILE_FILENAME = "provider_roles.json"
MAX_PROFILE_BYTES = 128 * 1024
PROFILE_ROLES = frozenset((*SPECIALIST_CREW_IDS, *SOFTWARE_TEAM_PERSONA_IDS))
SPECIALIST_ROLE_IDS = frozenset(SPECIALIST_CREW_IDS)
PERSONA_ROLE_IDS = frozenset(SOFTWARE_TEAM_PERSONA_IDS)
PROVIDER_IDS = frozenset((*PROVIDER_SPECS.keys(), "custom", "ollama"))


class ProviderRoleProfileError(ValueError):
    """A provider role profile is malformed or exceeds the reviewed contract."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderRoleProfileError("duplicate provider profile field")
        result[key] = value
    return result


def _text(value: Any, field: str, *, maximum: int = 200) -> str:
    if (
        not isinstance(value, str) or not value.strip()
        or len(value.strip()) > maximum or not value.isprintable()
    ):
        raise ProviderRoleProfileError(f"{field} must be non-empty printable text of at most {maximum} characters")
    return value.strip()


def _model_id(value: Any, field: str) -> str:
    result = _text(value, field)
    if result.casefold() in {"latest", "unknown", "unversioned"} or result.casefold().endswith(":latest"):
        raise ProviderRoleProfileError(f"{field} must identify a pinned model, not a mutable latest alias")
    return result


@dataclass(frozen=True, slots=True)
class RoleProviderBinding:
    role_id: str
    provider_id: str
    model_id: str
    timeout_seconds: int
    allowed_tools: tuple[str, ...]
    local_model_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "timeout_seconds": self.timeout_seconds,
            "allowed_tools": list(self.allowed_tools),
            "local_model_id": self.local_model_id,
        }


@dataclass(frozen=True, slots=True)
class ProviderRoleProfile:
    profile_id: str
    revision: int
    roles: Mapping[str, RoleProviderBinding]
    digest: str
    path: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "revision": self.revision,
            "roles": {
                role_id: self.roles[role_id].as_dict()
                for role_id in sorted(self.roles)
            },
            "digest": self.digest,
        }


def _parse_profile(raw: Any, path: Path) -> ProviderRoleProfile:
    if not isinstance(raw, Mapping):
        raise ProviderRoleProfileError("provider profile root must be an object")
    required = {"schema_version", "profile_id", "revision", "roles"}
    if set(raw) not in (required, required | {"digest"}):
        raise ProviderRoleProfileError("provider profile fields do not match the versioned schema")
    if raw.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ProviderRoleProfileError("unsupported provider role profile schema")
    profile_id = _text(raw.get("profile_id"), "profile_id", maximum=64)
    if re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", profile_id) is None:
        raise ProviderRoleProfileError("profile_id must be a lowercase identifier")
    revision = raw.get("revision")
    if type(revision) is not int or not 1 <= revision <= 1_000_000:
        raise ProviderRoleProfileError("revision must be an integer between 1 and 1000000")
    role_values = raw.get("roles")
    if not isinstance(role_values, Mapping) or not role_values:
        raise ProviderRoleProfileError("roles must be a non-empty object")
    unknown = set(role_values) - PROFILE_ROLES
    if unknown:
        raise ProviderRoleProfileError("unknown role IDs: " + ", ".join(sorted(unknown)))
    selected = set(role_values)
    for family_name, family in (
        ("specialist", SPECIALIST_ROLE_IDS),
        ("software-team persona", PERSONA_ROLE_IDS),
    ):
        present = selected & family
        if present and present != family:
            missing = sorted(family - present)
            raise ProviderRoleProfileError(
                f"{family_name} profile must configure the complete family; missing: " + ", ".join(missing)
            )
    roles: dict[str, RoleProviderBinding] = {}
    for role_id, item in role_values.items():
        if not isinstance(item, Mapping) or set(item) != {
            "provider_id", "model_id", "timeout_seconds", "allowed_tools", "local_model_id",
        }:
            raise ProviderRoleProfileError(f"role {role_id} fields do not match the profile schema")
        provider_id = _text(item.get("provider_id"), f"{role_id}.provider_id", maximum=64).lower()
        if provider_id not in PROVIDER_IDS:
            raise ProviderRoleProfileError(f"role {role_id} uses unsupported provider: {provider_id}")
        model_id = _model_id(item.get("model_id"), f"{role_id}.model_id")
        timeout = item.get("timeout_seconds")
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise ProviderRoleProfileError(f"role {role_id}.timeout_seconds must be between 1 and 120")
        tools = item.get("allowed_tools")
        if (
            not isinstance(tools, list) or len(tools) > 64
            or any(not isinstance(name, str) or not name.strip() or len(name) > 128 for name in tools)
            or len(set(tools)) != len(tools)
        ):
            raise ProviderRoleProfileError(f"role {role_id}.allowed_tools must be a unique list of at most 64 names")
        local_model = item.get("local_model_id")
        if provider_id == "ollama":
            if local_model is not None:
                raise ProviderRoleProfileError(f"role {role_id} must not set local_model_id when provider_id is ollama")
        elif local_model is not None:
            local_model = _model_id(local_model, f"{role_id}.local_model_id")
        roles[role_id] = RoleProviderBinding(
            role_id=role_id,
            provider_id=provider_id,
            model_id=model_id,
            timeout_seconds=timeout,
            allowed_tools=tuple(name.strip() for name in tools),
            local_model_id=local_model,
        )
    body = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": profile_id,
        "revision": revision,
        "roles": {key: roles[key].as_dict() for key in sorted(roles)},
    }
    digest = _digest(body)
    supplied_digest = raw.get("digest")
    if supplied_digest is not None and supplied_digest != digest:
        raise ProviderRoleProfileError("provider role profile digest does not match its content")
    return ProviderRoleProfile(profile_id, revision, roles, digest, path)


def load_provider_role_profile(path: str | Path) -> ProviderRoleProfile:
    """Load a bounded regular profile file and reject duplicate or unknown fields."""
    target = Path(path).expanduser()
    if target.is_symlink() or not target.is_file():
        raise ProviderRoleProfileError("provider role profile is missing or is not a regular file")
    if target.stat().st_size > MAX_PROFILE_BYTES:
        raise ProviderRoleProfileError("provider role profile exceeds the size limit")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderRoleProfileError("provider role profile is not valid UTF-8 JSON") from exc
    return _parse_profile(raw, target.resolve())


def default_provider_role_profile_path(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / ".auren" / DEFAULT_PROFILE_FILENAME


def create_provider_role_profile_document(
    *,
    profile_id: str = "engineering-default",
    revision: int = 1,
    provider_id: str = "ollama",
    model_id: str,
    local_model_id: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Create a complete starter profile; all tool scopes start empty."""
    profile_id = _text(profile_id, "profile_id", maximum=64)
    if re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", profile_id) is None:
        raise ProviderRoleProfileError("profile_id must be a lowercase identifier")
    if type(revision) is not int or not 1 <= revision <= 1_000_000:
        raise ProviderRoleProfileError("revision must be between 1 and 1000000")
    provider_id = _text(provider_id, "provider_id", maximum=64).lower()
    if provider_id not in PROVIDER_IDS:
        raise ProviderRoleProfileError(f"unsupported provider: {provider_id}")
    model_id = _model_id(model_id, "model_id")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
        raise ProviderRoleProfileError("timeout_seconds must be between 1 and 120")
    if provider_id == "ollama" and local_model_id is not None:
        raise ProviderRoleProfileError("local_model_id is not used when provider_id is ollama")
    if provider_id != "ollama" and local_model_id is not None:
        local_model_id = _model_id(local_model_id, "local_model_id")
    roles = {}
    for role_id in sorted(PROFILE_ROLES):
        roles[role_id] = {
            "provider_id": provider_id,
            "model_id": model_id,
            "timeout_seconds": timeout_seconds,
            "allowed_tools": [],
            "local_model_id": local_model_id,
        }
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": profile_id,
        "revision": revision,
        "roles": roles,
    }


def write_provider_role_profile(
    path: str | Path,
    document: Mapping[str, Any],
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or create a profile without overwriting an existing file."""
    target = Path(path).expanduser()
    parsed = _parse_profile(document, target)
    payload = _canonical(parsed.as_dict()) + b"\n"
    if not apply:
        return {
            "applied": False,
            "path": str(target),
            "profile_id": parsed.profile_id,
            "revision": parsed.revision,
            "profile_digest": parsed.digest,
            "role_count": len(parsed.roles),
            "preview_bytes": len(payload),
        }
    for parent in (target.parent, *target.parent.parents):
        if parent.is_symlink():
            raise ProviderRoleProfileError("refusing to write through a symlinked profile directory")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target.parent.is_symlink() or target.exists() or target.is_symlink():
        raise ProviderRoleProfileError("profile path exists or became unsafe; refusing to overwrite")
    temporary = target.with_name(target.name + ".tmp-" + str(os.getpid()))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists() or target.is_symlink():
            raise ProviderRoleProfileError("profile appeared during creation; refusing overwrite")
        os.replace(temporary, target)
        if os.name != "nt":
            target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return {
        "applied": True,
        "path": str(target),
        "profile_id": parsed.profile_id,
        "revision": parsed.revision,
        "profile_digest": parsed.digest,
        "role_count": len(parsed.roles),
    }


def _environment_for_binding(
    binding: RoleProviderBinding,
    environ: Mapping[str, str],
) -> dict[str, str]:
    env = dict(environ)
    if binding.provider_id == "custom":
        env["AUREN_CUSTOM_PROVIDER_MODEL"] = binding.model_id
        env["AUREN_MODEL_CUSTOM"] = binding.model_id
    elif binding.provider_id != "ollama":
        env["AUREN_MODEL_" + binding.provider_id.upper()] = binding.model_id
    return env


def _prompt_privacy(prompt: str) -> str:
    try:
        _prefix, separator, payload = prompt.partition("\n")
        if not separator:
            return "private"
        decoded = json.loads(payload)
        task_request = decoded.get("task_request", {}) if isinstance(decoded, Mapping) else {}
        if not isinstance(task_request, Mapping):
            return "private"
        value = task_request.get("privacy", task_request.get("data_classification", "private"))
        if not isinstance(value, str):
            return "private"
        normalized = value.strip().lower()
        return normalized if normalized in {"public", "non_sensitive"} else "private"
    except (ValueError, TypeError):
        return "private"


def _local_model_invoker(model_id: str):
    from .hybrid_model_router import HybridModelRouter, TaskRouteRequest
    from .muxin_session_adapter import LocalModelDiscovery

    router = HybridModelRouter(
        discovery=LocalModelDiscovery(),
        remote_executor=lambda _request: None,
    )

    def invoke(prompt: str, timeout_seconds: int) -> str:
        request = TaskRouteRequest(
            task=prompt,
            capability="general",
            privacy="private",
            max_attempts=1,
            timeout_seconds=float(timeout_seconds),
        )
        return router._execute_local(request, model_id)

    return invoke


def _role_invoker(
    binding: RoleProviderBinding,
    *,
    environ: Mapping[str, str],
    profile_binding: str,
):
    if binding.provider_id == "ollama":
        return _local_model_invoker(binding.model_id)

    env = _environment_for_binding(binding, environ)
    provider = OpenAICompatibleProvider.from_environment(binding.provider_id, environ=env)
    local = _local_model_invoker(binding.local_model_id) if binding.local_model_id else None
    remote_opt_in = env.get("AUREN_LOCAL_REMOTE_INFERENCE", "").strip().lower() == "enabled"
    workspace_classification = env.get("AUREN_LOCAL_DATA_CLASSIFICATION", "").strip().lower()
    remote_policy_ready = remote_opt_in and workspace_classification in {"public", "non_sensitive"}
    try:
        provider = OpenAICompatibleProvider.from_environment(binding.provider_id, environ=env)
    except (TypeError, ValueError):
        if local is None:
            raise
        provider = None

    def invoke(prompt: str, timeout_seconds: int) -> str:
        privacy = _prompt_privacy(prompt)
        if privacy not in {"public", "non_sensitive"}:
            if local is not None:
                return local(prompt, timeout_seconds)
            raise PermissionError("private or restricted task cannot use a remote provider profile")
        if not remote_policy_ready or provider is None:
            if local is not None:
                return local(prompt, timeout_seconds)
            raise PermissionError("remote inference is not enabled or configured for this workspace")
        response = provider.complete(prompt, timeout_seconds=float(timeout_seconds))
        return response.content

    return invoke


def provider_role_profile_report(
    profile: ProviderRoleProfile,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Report profile and credential readiness without exposing secret values."""
    if not isinstance(profile, ProviderRoleProfile):
        raise TypeError("profile must be a validated ProviderRoleProfile")
    env = os.environ if environ is None else environ
    remote_opt_in = env.get("AUREN_LOCAL_REMOTE_INFERENCE", "").strip().lower() == "enabled"
    workspace_classification = env.get("AUREN_LOCAL_DATA_CLASSIFICATION", "").strip().lower()
    remote_policy_ready = remote_opt_in and workspace_classification in {"public", "non_sensitive"}
    roles = []
    for role_id, binding in sorted(profile.roles.items()):
        if binding.provider_id == "ollama":
            configured = True
            credential_configured = False
            route = "local_only"
        else:
            try:
                OpenAICompatibleProvider.from_environment(
                    binding.provider_id,
                    environ=_environment_for_binding(binding, env),
                )
                configured = True
            except (TypeError, ValueError):
                configured = False
            credential_configured = (
                bool(env.get("AUREN_CUSTOM_PROVIDER_API_KEY"))
                if binding.provider_id == "custom"
                else bool(env.get(PROVIDER_SPECS[binding.provider_id].api_key_env))
            )
            route = "hybrid_local_private_remote_public" if binding.local_model_id else "remote_public_only"
        roles.append({
            "role_id": role_id,
            "provider_id": binding.provider_id,
            "model_id": binding.model_id,
            "local_model_id": binding.local_model_id,
            "configured": configured,
            "credential_configured": credential_configured,
            "allowed_tools": list(binding.allowed_tools),
            "timeout_seconds": binding.timeout_seconds,
            "route": route,
            "route_policy_ready": configured and (binding.provider_id == "ollama" or remote_policy_ready or binding.local_model_id is not None),
            "authenticated": "not_tested",
            "live_tested": "not_tested",
        })
    return {
        "status": "valid_profile",
        "path": str(profile.path),
        "profile_id": profile.profile_id,
        "revision": profile.revision,
        "profile_digest": profile.digest,
        "role_count": len(profile.roles),
        "roles": roles,
        "remote_policy_ready": remote_policy_ready,
        "private_data_routing": "local_only",
        "configuration_ready": all(item["configured"] for item in roles),
        "route_policy_ready": all(item["route_policy_ready"] for item in roles),
        "ready_for_live_validation": all(item["configured"] and item["route_policy_ready"] for item in roles),
        "live_activation": "not_performed",
        "authority_note": "provider/model routing only; tools, Guard, approvals and task authority remain host-owned",
    }


def build_profiled_crew_adapter_inputs(
    profile_path: str | Path,
    *,
    tools: ApprovedToolRegistry,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build complete, secret-free adapter maps for the selected role families.

    This prepares adapters but does not register them. The runtime host must
    supply its canonical ApprovedToolRegistry and perform one atomic registration.
    """
    if not isinstance(tools, ApprovedToolRegistry):
        raise TypeError("tools must be the canonical ApprovedToolRegistry")
    profile = load_provider_role_profile(profile_path)
    env = os.environ if environ is None else environ
    specialists = {}
    personas = {}
    for role_id, binding in profile.roles.items():
        missing_tools = set(binding.allowed_tools) - set(tools.names)
        if missing_tools:
            raise ProviderRoleProfileError(
                f"role {role_id} requests tools absent from the host-approved registry: "
                + ", ".join(sorted(missing_tools))
            )
        invoker = _role_invoker(binding, environ=env)
        provider_id = binding.provider_id
        provider_version = f"{binding.model_id}+profile-{profile.digest[:12]}"
        if binding.local_model_id:
            provider_id = f"{provider_id}+ollama"
            provider_version = (
                f"remote-{binding.model_id}+local-{binding.local_model_id}"
                f"+profile-{profile.digest[:12]}"
            )
        entry = {
            "invoker": invoker,
            "provider_id": provider_id,
            "provider_version": provider_version,
            "allowed_tools": binding.allowed_tools,
            "timeout_seconds": binding.timeout_seconds,
        }
        if role_id in SPECIALIST_ROLE_IDS:
            specialists[role_id] = entry
        else:
            personas[role_id] = entry
    return {
        "profile_id": profile.profile_id,
        "profile_revision": profile.revision,
        "profile_digest": profile.digest,
        "specialists": specialists,
        "personas": personas,
    }


__all__ = [
    "DEFAULT_PROFILE_FILENAME", "PERSONA_ROLE_IDS", "PROFILE_ROLES",
    "PROFILE_SCHEMA_VERSION", "ProviderRoleProfile", "ProviderRoleProfileError",
    "RoleProviderBinding", "SPECIALIST_ROLE_IDS",
    "build_profiled_crew_adapter_inputs", "create_provider_role_profile_document",
    "default_provider_role_profile_path", "load_provider_role_profile",
    "provider_role_profile_report", "write_provider_role_profile",
]
