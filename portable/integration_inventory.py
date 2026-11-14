"""Read-only inventory of AUREN hosts, model providers and optional integrations.

Detection is not authentication, live acceptance, trust, or permission. This
module reports those states separately and never returns credential values.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tomllib
from typing import Any, Mapping

_MAX_CONFIG_BYTES = 2 * 1024 * 1024


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def _nested(value: Mapping[str, Any], dotted_key: str) -> Any:
    current: Any = value
    for part in dotted_key.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current



def _strip_jsonc(raw: str) -> str:
    """Remove JSONC comments and trailing commas without touching string values."""
    without_comments = []
    index = 0
    in_string = False
    escaped = False
    while index < len(raw):
        char = raw[index]
        following = raw[index + 1] if index + 1 < len(raw) else ""
        if in_string:
            without_comments.append(char)
            if escaped:
                escaped = False
            elif char == "\\\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            without_comments.append(char)
            index += 1
        elif char == "/" and following == "/":
            index += 2
            while index < len(raw) and raw[index] not in "\\r\\n":
                index += 1
        elif char == "/" and following == "*":
            index += 2
            while index + 1 < len(raw) and raw[index:index + 2] != "*/":
                index += 1
            if index + 1 >= len(raw):
                raise ValueError("unterminated JSONC block comment")
            index += 2
        else:
            without_comments.append(char)
            index += 1

    source = "".join(without_comments)
    result = []
    index = 0
    in_string = False
    escaped = False
    while index < len(source):
        char = source[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
        elif char == ",":
            lookahead = index + 1
            while lookahead < len(source) and source[lookahead].isspace():
                lookahead += 1
            if lookahead >= len(source) or source[lookahead] not in "}]":
                result.append(char)
            index += 1
        else:
            result.append(char)
            index += 1
    return "".join(result)

def _client_configuration(client: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(str(client.get("path", ""))).expanduser()
    base = {
        "name": str(client.get("name", "unknown")),
        "path": str(path),
        "detected": True,
        "configured": False,
        "authenticated": "not_tested",
        "live_tested": "not_tested",
        "enabled": "not_verified",
    }
    if path.is_symlink():
        return {**base, "status": "blocked_symlink"}
    if not path.exists():
        return {**base, "status": "detected_not_configured"}
    if not path.is_file() or path.stat().st_size > _MAX_CONFIG_BYTES:
        return {**base, "status": "blocked_invalid_config"}
    try:
        raw = path.read_text(encoding="utf-8")
        if client.get("format") == "toml":
            parsed = tomllib.loads(raw)
        else:
            if client.get("format") == "jsonc" or str(client.get("name", "")).startswith("VS Code"):
                raw = _strip_jsonc(raw)
            parsed = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        return {**base, "status": "blocked_unreadable_config"}
    if not isinstance(parsed, Mapping):
        return {**base, "status": "blocked_invalid_config"}
    server_map = _nested(parsed, str(client.get("key", "")))
    entry = server_map.get("auren") if isinstance(server_map, Mapping) else None
    configured = isinstance(entry, Mapping) and bool(entry)
    return {
        **base,
        "configured": configured,
        "status": "configured_not_live_tested" if configured else "detected_not_configured",
    }


def _remote_provider_inventory(environ: Mapping[str, str]) -> dict[str, Any]:
    from .remote_model_providers import PROVIDER_SPECS, provider_configuration_report

    configured_report = provider_configuration_report(environ=environ)
    order = configured_report.get("provider_order", [])
    ordered = set(order) if isinstance(order, list) else set()
    order_valid = not configured_report.get("errors") and bool(order)
    remote_opt_in = environ.get("AUREN_LOCAL_REMOTE_INFERENCE", "").strip().lower() == "enabled"
    classification = environ.get("AUREN_LOCAL_DATA_CLASSIFICATION", "").strip().lower()
    privacy_ready = classification in {"public", "non_sensitive"}
    providers = []
    for name, spec in sorted(PROVIDER_SPECS.items()):
        credential_present = bool(environ.get(spec.api_key_env, ""))
        model = environ.get("AUREN_MODEL_" + name.upper(), environ.get(spec.model_env, "")).strip()
        configured = credential_present and bool(model)
        enabled = configured and name in ordered and order_valid and remote_opt_in and privacy_ready
        providers.append({
            "id": name,
            "kind": "remote_model_api",
            "detected": True,
            "configured": configured,
            "credential_configured": credential_present,
            "model_configured": bool(model),
            "model_id": model or None,
            "authenticated": "not_tested",
            "live_tested": "not_tested",
            "enabled": enabled,
            "status": (
                "enabled_not_live_tested" if enabled
                else "configured_but_disabled" if configured
                else "detected_not_configured"
            ),
        })
    if "custom" in ordered or any(environ.get(key) for key in (
        "AUREN_CUSTOM_PROVIDER_API_KEY", "AUREN_CUSTOM_PROVIDER_URL",
        "AUREN_CUSTOM_PROVIDER_MODEL",
    )):
        custom = next(
            (item for item in configured_report.get("providers", []) if item.get("id") == "custom"),
            {},
        )
        configured = custom.get("status") == "configured_not_live_validated"
        enabled = configured and order_valid and remote_opt_in and privacy_ready and "custom" in ordered
        providers.append({
            "id": "custom",
            "kind": "custom_openai_compatible_api",
            "detected": True,
            "configured": configured,
            "credential_configured": bool(environ.get("AUREN_CUSTOM_PROVIDER_API_KEY")),
            "model_configured": bool(environ.get("AUREN_CUSTOM_PROVIDER_MODEL")),
            "model_id": environ.get("AUREN_CUSTOM_PROVIDER_MODEL") or None,
            "endpoint_configured": bool(custom.get("endpoint_configured")),
            "authenticated": "not_tested",
            "live_tested": "not_tested",
            "enabled": enabled,
            "status": "enabled_not_live_tested" if enabled else (
                "configured_but_disabled" if configured else "detected_not_configured"
            ),
        })
    return {
        "providers": providers,
        "configured_order": order,
        "remote_inference_opt_in": remote_opt_in,
        "workspace_classification": classification or None,
        "privacy_policy_ready": privacy_ready,
        "configuration_errors": configured_report.get("errors", []),
        "authenticated": "not_tested",
        "live_tested": "not_tested",
    }


def collect_integration_inventory(
    project_root: str | Path = ".",
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a read-only, credential-safe integration inventory."""
    from . import onboarding

    root = Path(project_root).expanduser().resolve()
    user_home = Path(home or Path.home()).expanduser()
    env = os.environ if environ is None else environ
    doctor = onboarding.doctor(root)
    clients = [
        _client_configuration(item)
        for item in onboarding.discover_clients(user_home)
    ]
    remote = _remote_provider_inventory(env)

    cli_providers = []
    for item in doctor.get("providers", []):
        if not isinstance(item, Mapping):
            continue
        cli_providers.append({
            "id": item.get("id"),
            "kind": "provider_cli",
            "detected": True,
            "configured": bool(item.get("command")),
            "command": item.get("command"),
            "version": item.get("version"),
            "capabilities": item.get("capabilities", []),
            "authenticated": "not_tested",
            "live_tested": "not_tested",
            "enabled": "not_verified",
            "status": item.get("status", "detected_not_live_validated"),
        })

    local_model = doctor.get("local_model", {})
    if not isinstance(local_model, Mapping):
        local_model = {}
    local_models = local_model.get("models", [])
    if not isinstance(local_models, list):
        local_models = []
    local_provider = {
        "id": "ollama-compatible",
        "kind": "local_model_runtime",
        "detected": bool(local_model.get("available")),
        "configured": bool(local_models),
        "model_ids": [
            item.get("name") for item in local_models
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        ],
        "authenticated": "not_applicable",
        "live_tested": "not_tested",
        "enabled": bool(local_model.get("available")),
        "status": local_model.get("status", "not_probed"),
    }

    manifests = []
    manifest_root = user_home / ".auren" / "providers"
    if not manifest_root.is_symlink() and manifest_root.is_dir():
        for path in sorted(manifest_root.glob("*.json"))[:50]:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
                continue
            manifests.append({
                "id": path.stem,
                "path": str(path),
                "kind": "provider_capability_manifest",
                "detected": True,
                "trust": "untrusted_metadata_only",
                "enabled": False,
                "status": "discovered_not_executed",
            })

    role_profile: dict[str, Any] = {"status": "not_configured"}
    profile_path = root / ".auren" / "provider_roles.json"
    if profile_path.exists() or profile_path.is_symlink():
        try:
            from .provider_role_profiles import load_provider_role_profile, provider_role_profile_report
            profile = load_provider_role_profile(profile_path)
            role_profile = provider_role_profile_report(profile, environ=env)
        except Exception as exc:
            role_profile = {
                "status": "blocked_invalid_profile",
                "path": str(profile_path),
                "error_type": type(exc).__name__,
            }


    skill_paths = (
        root / "skills" / "ai-coding-orchestrator" / "SKILL.md",
        root / ".agents" / "skills" / "ai-coding-orchestrator" / "SKILL.md",
        root / ".claude" / "skills" / "ai-coding-orchestrator" / "SKILL.md",
        root / ".gemini" / "skills" / "ai-coding-orchestrator" / "SKILL.md",
        user_home / ".agents" / "skills" / "ai-coding-orchestrator" / "SKILL.md",
        user_home / ".claude" / "skills" / "ai-coding-orchestrator" / "SKILL.md",
        user_home / ".gemini" / "skills" / "ai-coding-orchestrator" / "SKILL.md",
    )
    agent_skills = [
        {
            "id": "ai-coding-orchestrator",
            "path": str(path),
            "detected": True,
            "installed": path == user_home or user_home in path.parents,
            "status": "present_not_host_loaded",
        }
        for path in skill_paths
        if not path.is_symlink() and path.is_file()
    ]

    hosts = []
    for index, item in enumerate(clients):
        hosts.append({
            "id": f"mcp-host-{index + 1}",
            "kind": "mcp_host",
            **item,
        })

    summary = {
        "mcp_hosts_detected": len(hosts),
        "mcp_hosts_configured": sum(item["configured"] for item in hosts),
        "remote_providers_configured": sum(item["configured"] for item in remote["providers"]),
        "remote_providers_enabled": sum(item["enabled"] is True for item in remote["providers"]),
        "provider_clis_detected": len(cli_providers),
        "local_models_detected": len(local_provider["model_ids"]),
        "capability_manifests_detected": len(manifests),
        "agent_skill_copies_detected": len(agent_skills),
    }
    return {
        "schema_version": "auren-integration-inventory-v1",
        "project_root": str(root),
        "discovery_policy": "read_only_no_client_config_changes_no_remote_model_api_requests",
        "status_semantics": {
            "detected": "A local executable, config, endpoint or manifest was observed.",
            "configured": "Required local configuration is present; this does not prove authentication.",
            "authenticated": "not_tested unless an explicit credential check is implemented.",
            "live_tested": "not_tested unless a versioned acceptance receipt exists.",
            "enabled": "AUREN policy and configuration permit use; it is not a quality or safety verdict.",
        },
        "mcp_hosts": hosts,
        "provider_clis": cli_providers,
        "remote_model_providers": remote,
        "local_model_provider": local_provider,
        "vibe_coding_host": doctor.get("vibe_coding_host", {"status": "not_reported"}),
        "provider_capability_manifests": manifests,
        "agent_skills": agent_skills,
        "graph_team_provider_profile": role_profile,
        "graph_team_runtime_registration": "not_observable_from_read_only_cli",
        "summary": summary,
    }


__all__ = ["collect_integration_inventory"]
