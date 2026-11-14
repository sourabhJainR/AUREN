"""Safe, read-only repository discovery and opt-in MCP client configuration.

This module never executes repository scripts and never overwrites unrelated
client configuration. Changes are previewed by default and applied only with
an explicit --apply flag.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import stat
import tomllib
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any

_MAX_SCAN_FILES = 5000
_MAX_CONFIG_BYTES = 2 * 1024 * 1024
_IGNORED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}

class OnboardingError(RuntimeError):
    pass


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OnboardingError(f"duplicate JSON key in client configuration: {key}")
        result[key] = value
    return result


def _safe_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise OnboardingError(f"client configuration is not a regular file: {path}")
    if path.stat().st_size > _MAX_CONFIG_BYTES:
        raise OnboardingError(f"client configuration exceeds {_MAX_CONFIG_BYTES} bytes: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OnboardingError(f"cannot safely parse client configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OnboardingError(f"client configuration root must be an object: {path}")
    return value


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            timeout=3, check=False, env={"PATH": os.environ.get("PATH", "")},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def inspect_repository(project_root: str | Path) -> dict[str, Any]:
    """Inspect a repository without running its code or changing its files."""
    root = Path(project_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise OnboardingError("project root must be a directory")
    files: list[str] = []
    directories: list[str] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(current)
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in _IGNORED_DIRS and not (base / name).is_symlink()
        )
        depth = len(base.relative_to(root).parts)
        if depth > 5:
            dirnames[:] = []
            continue
        for filename in sorted(filenames):
            path = base / filename
            if path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            files.append(rel)
            if len(files) >= _MAX_SCAN_FILES:
                break
        if len(files) >= _MAX_SCAN_FILES:
            break
    names = set(files)
    languages = []
    for language, markers in {
        "python": ("pyproject.toml", "requirements.txt", "setup.py"),
        "javascript": ("package.json",),
        "typescript": ("tsconfig.json",),
        "rust": ("Cargo.toml",),
        "go": ("go.mod",),
        "java": ("pom.xml", "build.gradle", "build.gradle.kts"),
        "dotnet": (".sln", ".csproj"),
        "ruby": ("Gemfile",),
    }.items():
        if any(Path(item).name in markers for item in names):
            languages.append(language)
    root_files = sorted(item for item in files if "/" not in item)
    instructions = [name for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "COPILOT_INSTRUCTIONS.md") if name in names]
    return {
        "schema_version": "auren-repository-inspection-v1",
        "project_root": str(root),
        "git": {
            "is_repository": _git(root, "rev-parse", "--show-toplevel") is not None,
            "root": _git(root, "rev-parse", "--show-toplevel"),
            "branch": _git(root, "branch", "--show-current"),
            "head": _git(root, "rev-parse", "HEAD"),
            "dirty": bool(_git(root, "status", "--porcelain")),
        },
        "languages": languages,
        "root_files": root_files[:200],
        "instruction_files": instructions,
        "build_and_ci_signals": [
            item for item in files
            if item in {"Makefile", "justfile", "tox.ini", "noxfile.py", "pytest.ini"}
            or item.startswith(".github/workflows/")
        ][:200],
        "file_count": len(files),
        "scan_truncated": len(files) >= _MAX_SCAN_FILES,
        "inspection_policy": "read_only_no_repository_scripts_executed",
    }


def initialize_repository(project_root: str | Path, *, apply: bool = True) -> dict[str, Any]:
    """Create only .auren/project.json; refuse symlink and overwrite cases."""
    report = inspect_repository(project_root)
    root = Path(report["project_root"])
    state_dir = root / ".auren"
    if state_dir.is_symlink():
        raise OnboardingError(".auren must not be a symlink")
    profile_path = state_dir / "project.json"
    if profile_path.exists() or profile_path.is_symlink():
        raise OnboardingError(f"refusing to overwrite existing project profile: {profile_path}")
    profile = {
        "schema_version": "auren-project-profile-v1",
        "project_root": str(root),
        "created_by": "auren init",
        "repository_inspection": report,
        "workflow_profile": "safe_default",
        "execution_authority": "host_managed_only",
        "runtime_mode": "single_user_local",
        "managed_runtime": {"enabled": False, "runtime_factory": None},
    }
    if not apply:
        return {"created": False, "preview": profile, "path": str(profile_path)}
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state_dir.is_symlink():
        raise OnboardingError(".auren became a symlink during initialization")
    if os.name != "nt":
        state_dir.chmod(0o700)
    temporary = state_dir / ".project.json.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(profile, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if profile_path.exists() or profile_path.is_symlink():
            raise OnboardingError("project profile appeared during initialization; refusing overwrite")
        os.replace(temporary, profile_path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return {"created": True, "path": str(profile_path), "inspection": report}



_RUNTIME_MODES = {"single_user_local", "managed"}


def load_project_profile(project_root: str | Path) -> tuple[Path, dict[str, Any]]:
    root = Path(project_root).expanduser().resolve(strict=True)
    state_dir = root / ".auren"
    if state_dir.is_symlink():
        raise OnboardingError(".auren must not be a symlink")
    profile_path = state_dir / "project.json"
    if profile_path.is_symlink() or not profile_path.is_file():
        raise OnboardingError("AUREN project profile is missing or is not a regular file; run auren init first")
    profile = _safe_json(profile_path)
    if profile.get("schema_version") != "auren-project-profile-v1":
        raise OnboardingError("unsupported AUREN project profile schema")
    mode = profile.get("runtime_mode", "single_user_local")
    if not isinstance(mode, str) or mode not in _RUNTIME_MODES:
        raise OnboardingError("project profile has an unsupported runtime_mode")
    managed = profile.get("managed_runtime", {"enabled": mode == "managed", "runtime_factory": None})
    if not isinstance(managed, dict) or type(managed.get("enabled")) is not bool:
        raise OnboardingError("managed_runtime configuration is invalid")
    factory = managed.get("runtime_factory")
    if factory is not None and (
        not isinstance(factory, str) or ":" not in factory
        or any(not part.strip() for part in factory.split(":", 1))
        or any(ch.isspace() for ch in factory)
    ):
        raise OnboardingError("managed_runtime.runtime_factory must use module.path:function")
    if mode == "single_user_local" and (
        managed.get("enabled") is not False or factory is not None
    ):
        raise OnboardingError("single-user local mode cannot enable or retain managed runtime components")
    if mode == "managed" and managed.get("enabled") is not True:
        raise OnboardingError("managed mode requires explicit managed_runtime.enabled=true")
    return profile_path, profile


def set_runtime_mode(
    project_root: str | Path, mode: str, *, apply: bool = False,
    runtime_factory: str | None = None,
) -> dict[str, Any]:
    """Preview or apply an explicit local/managed runtime choice.

    Single-user local is the default and never accepts a runtime factory.
    Managed mode is an explicit opt-in and may name an advanced trusted factory.
    """
    if not isinstance(mode, str) or mode not in _RUNTIME_MODES:
        raise OnboardingError(f"runtime mode must be one of: {', '.join(sorted(_RUNTIME_MODES))}")
    if mode == "single_user_local" and runtime_factory:
        raise OnboardingError("runtime factories are not part of single-user local setup")
    if runtime_factory is not None and (
        not isinstance(runtime_factory, str) or ":" not in runtime_factory
        or any(not part.strip() for part in runtime_factory.split(":", 1))
        or any(ch.isspace() for ch in runtime_factory)
    ):
        raise OnboardingError("runtime factory must use module.path:function_name")
    path, profile = load_project_profile(project_root)
    updated = dict(profile)
    updated["runtime_mode"] = mode
    updated["managed_runtime"] = {
        "enabled": mode == "managed",
        "runtime_factory": runtime_factory if mode == "managed" else None,
    }
    if not apply:
        return {"applied": False, "path": str(path), "profile": updated}
    backup = _atomic_config_write(
        path, (json.dumps(updated, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return {"applied": True, "path": str(path), "backup": str(backup), "runtime_mode": mode}



def discover_clients(home: Path | None = None) -> list[dict[str, str]]:
    base = (home or Path.home()).expanduser()
    candidates = [
        ("Claude Desktop", base / "Library/Application Support/Claude/claude_desktop_config.json", "mcpServers"),
        ("Claude Desktop", base / "AppData/Roaming/Claude/claude_desktop_config.json", "mcpServers"),
        ("Claude Desktop", base / ".config/Claude/claude_desktop_config.json", "mcpServers"),
        ("Claude Code", base / ".claude.json", "mcpServers"),
        ("Gemini CLI", base / ".gemini/settings.json", "mcpServers"),
        ("Cursor", base / ".cursor/mcp.json", "mcpServers"),
        ("Windsurf", base / ".codeium/windsurf/mcp_config.json", "mcpServers"),
        ("VS Code", base / ".config/Code/User/settings.json", "mcp.servers"),
        ("VS Code Insiders", base / ".config/Code - Insiders/User/settings.json", "mcp.servers"),
        ("VS Code Insiders", base / "Library/Application Support/Code - Insiders/User/settings.json", "mcp.servers"),
        ("VS Code", base / "Library/Application Support/Code/User/settings.json", "mcp.servers"),
        ("VS Code", base / "AppData/Roaming/Code/User/settings.json", "mcp.servers"),
        ("VS Code Insiders", base / "AppData/Roaming/Code - Insiders/User/settings.json", "mcp.servers"),
    ]
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    executable_by_name = {
        "Claude Code": "claude", "Gemini CLI": "gemini",
        "Cursor": "cursor", "Windsurf": "windsurf",
        "VS Code": "code", "VS Code Insiders": "code-insiders",
    }
    for name, path, key in candidates:
        executable = executable_by_name.get(name)
        if (path.exists() or (executable and shutil.which(executable))) and str(path) not in seen:
            seen.add(str(path))
            found.append({"name": name, "path": str(path), "key": key})
    codex_config = base / ".codex" / "config.toml"
    if codex_config.exists() or shutil.which("codex"):
        found.append({"name": "Codex CLI / app", "path": str(codex_config),
                      "key": "mcp_servers", "format": "toml"})
    return found


def _get_nested(value: dict[str, Any], key: str) -> dict[str, Any]:
    parts = key.split(".")
    current = value
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise OnboardingError(f"configuration field {part} is not an object")
        current = child
    final = current.setdefault(parts[-1], {})
    if not isinstance(final, dict):
        raise OnboardingError(f"configuration field {key} is not an object")
    return final


def _atomic_config_write(path: Path, content: bytes) -> Path:
    # Inspect existing ancestors before mkdir so a symlinked parent is never
    # traversed as a side effect of configuration setup.
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise OnboardingError(f"refusing to write through symlinked client configuration directory: {parent}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OnboardingError(f"refusing to write through client configuration symlink: {path}")
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise OnboardingError(f"refusing to write through symlinked client configuration directory: {parent}")
    backup = path.with_name(path.name + f".auren-backup-{time.time_ns()}")
    if path.exists():
        if not path.is_file():
            raise OnboardingError(f"client configuration is not a regular file: {path}")
        shutil.copy2(path, backup)
    temporary = path.with_name(path.name + f".auren-tmp-{time.time_ns()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        if backup.exists():
            shutil.copy2(backup, path)
        raise
    return backup


def _local_proxy_command(mcp_url: str, token: Path) -> tuple[str, list[str]]:
    executable = shutil.which("auren")
    if executable:
        return executable, ["mcp-proxy", "--url", mcp_url, "--token-file", str(token)]
    return sys.executable, [str(Path(__file__).resolve().parents[1] / "auren_cli.py"),
                            "mcp-proxy", "--url", mcp_url, "--token-file", str(token)]


def _configure_toml_client(client: dict[str, str], *, command: str, args: list[str],
                           apply: bool) -> dict[str, Any]:
    path = Path(client["path"]).expanduser()
    if path.is_symlink():
        raise OnboardingError(f"refusing to write through client configuration symlink: {path}")
    if path.exists():
        if not path.is_file() or path.stat().st_size > _MAX_CONFIG_BYTES:
            raise OnboardingError(f"Codex configuration is not a bounded regular file: {path}")
        try:
            original = path.read_bytes()
            parsed = tomllib.loads(original.decode("utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise OnboardingError(f"cannot safely parse Codex TOML configuration {path}: {exc}") from exc
    else:
        original, parsed = b"", {}
    servers = parsed.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise OnboardingError("Codex mcp_servers configuration must be a TOML table")
    desired = {"command": command, "args": args}
    previous = servers.get("auren")
    if previous == desired:
        return {"client": client["name"], "path": str(path), "status": "already_configured"}
    if previous is not None:
        return {"client": client["name"], "path": str(path), "status": "conflict",
                "reason": "an existing Codex AUREN entry was left unchanged"}
    block = ("\n[mcp_servers.auren]\n"
             + "command = " + json.dumps(command, ensure_ascii=False) + "\n"
             + "args = " + json.dumps(args, ensure_ascii=False) + "\n")
    if not apply:
        return {"client": client["name"], "path": str(path), "status": "preview",
                "proposed_entry": desired}
    content = original + (b"" if not original or original.endswith(b"\n") else b"\n")
    backup = _atomic_config_write(path, content + block.encode("utf-8"))
    return {"client": client["name"], "path": str(path), "status": "configured",
            "backup": str(backup) if backup.exists() else None}


def configure_clients(
    *, mcp_url: str, token_file: str | Path, apply: bool = False,
    clients: list[dict[str, str]] | None = None, best_effort: bool = False,
) -> dict[str, Any]:
    """Preview or apply local MCP config; isolate client failures in best-effort mode."""
    parsed_url = urlsplit(mcp_url)
    if (parsed_url.scheme != "http" or parsed_url.hostname != "127.0.0.1"
        or parsed_url.port is None or parsed_url.path != "/mcp"
        or parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment):
        raise OnboardingError("local MCP URL must be an explicit http://127.0.0.1:<port>/mcp endpoint")
    token = Path(token_file).expanduser()
    if token.is_symlink() or not token.is_file():
        raise OnboardingError("local MCP credential is not present; start AUREN local services first")
    if os.name != "nt" and stat.S_IMODE(token.stat().st_mode) & 0o077:
        raise OnboardingError("local MCP credential permissions must be owner-only (0600)")
    command, args = _local_proxy_command(mcp_url, token)
    server = {"command": command, "args": args}
    outcomes: list[dict[str, Any]] = []
    for client in clients if clients is not None else discover_clients():
        path = Path(client["path"]).expanduser()
        try:
            if client.get("format") == "toml":
                outcomes.append(_configure_toml_client(client, command=command, args=args, apply=apply))
                continue
            current = _safe_json(path) if path.exists() else {}
            key = client["key"]
            previous = _get_nested(current, key).get("auren")
            if previous == server:
                outcomes.append({"client": client["name"], "path": str(path), "status": "already_configured"})
                continue
            preview = dict(current)
            _get_nested(preview, key)["auren"] = server
            if not apply:
                outcomes.append({"client": client["name"], "path": str(path), "status": "preview",
                                 "existing_auren_entry": previous, "proposed_entry": server})
                continue
            backup = _atomic_config_write(
                path, (json.dumps(preview, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
            outcomes.append({"client": client["name"], "path": str(path), "status": "configured",
                             "backup": str(backup) if backup.exists() else None})
        except (OSError, ValueError, RuntimeError) as exc:
            if not best_effort:
                raise
            outcomes.append({"client": client.get("name", "unknown"), "path": str(path),
                             "status": "blocked", "reason": str(exc)})
    return {"applied": apply, "clients": outcomes,
            "configured_count": sum(x["status"] == "configured" for x in outcomes),
            "blocked_count": sum(x["status"] in {"blocked", "conflict"} for x in outcomes)}


def doctor(project_root: str | Path = ".") -> dict[str, Any]:
    """Report runtime, local-model and direct API configuration without exposing secrets."""
    root = Path(project_root).expanduser().resolve()
    runtime_profile: dict[str, Any] = {
        "mode": "single_user_local",
        "managed_runtime_enabled": False,
        "runtime_factory": "not_used_for_single_user_local_setup",
    }
    try:
        _profile_path, profile = load_project_profile(root)
        runtime_profile = {
            "mode": profile.get("runtime_mode", "single_user_local"),
            "managed_runtime_enabled": profile.get("runtime_mode") == "managed",
            "runtime_factory": (
                profile.get("managed_runtime", {}).get("runtime_factory")
                if profile.get("runtime_mode") == "managed" else "not_used_for_single_user_local_setup"
            ),
        }
    except OnboardingError as exc:
        runtime_profile["profile_error"] = str(exc)
    report: dict[str, Any] = {
        "project": str(root),
        "runtime_profile": runtime_profile,
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
        "local_http_dependency": {"available": False, "package": "uvicorn"},
        "providers": [],
        "remote_model_providers": {
            "enabled": False, "status": "disabled", "provider_order": [],
            "providers": [], "errors": [],
        },
        "local_model": {
            "available": False, "provider": "ollama-compatible",
            "models": [], "status": "not_probed",
        },
        "vibe_coding_host": {"status": "not_configured"},
        "remote_inference_policy": {
            "enabled": os.environ.get("AUREN_LOCAL_REMOTE_INFERENCE", "").strip().lower() == "enabled",
            "data_classification": os.environ.get("AUREN_LOCAL_DATA_CLASSIFICATION", "").strip().lower() or None,
        },
        "runtime_factory": {
            "required_for_single_user_local": False,
            "status": "advanced_managed_extension_only",
        },
        "next_action": "Connect a local model or configure one or more direct API providers, then run a live conformance probe before using remote inference.",
    }
    try:
        import uvicorn  # noqa: F401
        report["local_http_dependency"]["available"] = True
    except ImportError:
        pass
    try:
        from .provider_fabric import ProviderFabric
        report["providers"] = [
            {
                "id": name, "command": item.command, "version": item.version,
                "capabilities": list(item.capabilities),
                "status": "detected_not_live_validated",
            }
            for name, item in sorted(ProviderFabric().discover().items())
        ]
    except Exception as exc:
        report["provider_discovery_error"] = type(exc).__name__
    try:
        from .remote_model_providers import provider_configuration_report
        report["remote_model_providers"] = provider_configuration_report()
    except Exception as exc:
        report["remote_provider_configuration_error"] = type(exc).__name__
    try:
        from .vibe_coding_host import inspect_vibe_host_readiness, load_host_config
        host_config_path = Path.home().expanduser() / ".auren" / "vibe-coding" / "host.json"
        host_config = load_host_config(host_config_path)
        report["vibe_coding_host"] = inspect_vibe_host_readiness(host_config)
    except FileNotFoundError:
        report["vibe_coding_host"] = {"status": "not_configured"}
    except Exception as exc:
        report["vibe_coding_host"] = {"status": "blocked", "reason": type(exc).__name__}
    try:
        from .muxin_session_adapter import LocalModelDiscovery
        local = LocalModelDiscovery().discover()
        report["local_model"] = {
            **local,
            "status": "detected_not_quality_validated" if local.get("available") else "not_available",
        }
    except Exception as exc:
        report["local_model"] = {
            "available": False, "provider": "ollama-compatible",
            "models": [], "status": "probe_failed", "reason": type(exc).__name__,
        }
    report["repository"] = inspect_repository(root)
    return report
