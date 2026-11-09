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


def discover_clients(home: Path | None = None) -> list[dict[str, str]]:
    base = (home or Path.home()).expanduser()
    candidates = [
        ("Claude Desktop", base / "Library/Application Support/Claude/claude_desktop_config.json", "mcpServers"),
        ("Claude Desktop", base / "AppData/Roaming/Claude/claude_desktop_config.json", "mcpServers"),
        ("Claude Code", base / ".claude.json", "mcpServers"),
        ("Gemini CLI", base / ".gemini/settings.json", "mcpServers"),
        ("Cursor", base / ".cursor/mcp.json", "mcpServers"),
        ("VS Code", base / ".config/Code/User/settings.json", "mcp.servers"),
        ("VS Code", base / "Library/Application Support/Code/User/settings.json", "mcp.servers"),
        ("VS Code", base / "AppData/Roaming/Code/User/settings.json", "mcp.servers"),
        ("VS Code Insiders", base / "AppData/Roaming/Code - Insiders/User/settings.json", "mcp.servers"),
    ]
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for name, path, key in candidates:
        if path.exists() and str(path) not in seen:
            seen.add(str(path))
            found.append({"name": name, "path": str(path), "key": key})
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
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OnboardingError(f"refusing to write through client configuration symlink: {path}")
    backup = path.with_name(path.name + f".auren-backup-{time.time_ns()}")
    if path.exists():
        if not path.is_file():
            raise OnboardingError(f"client configuration is not a regular file: {path}")
        shutil.copy2(path, backup)
    temporary = path.with_name(path.name + ".auren-tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
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


def configure_clients(
    *, mcp_url: str, token_file: str | Path, apply: bool = False,
    clients: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Preview or apply only the AUREN MCP server entry in supported JSON hosts."""
    if not mcp_url.startswith("http://127.0.0.1:") or not mcp_url.endswith("/mcp"):
        raise OnboardingError("local MCP URL must be an explicit 127.0.0.1 HTTP endpoint ending in /mcp")
    token = Path(token_file).expanduser()
    if token.is_symlink() or not token.is_file():
        raise OnboardingError("local MCP credential is not present; start AUREN local services first")
    proxy = shutil.which("auren")
    if proxy:
        command = proxy
        args = ["mcp-proxy", "--url", mcp_url, "--token-file", str(token)]
    else:
        command = sys.executable
        args = [str(Path(__file__).resolve().parents[1] / "auren_cli.py"), "mcp-proxy",
                "--url", mcp_url, "--token-file", str(token)]
    server = {"command": command, "args": args}
    outcomes = []
    for client in clients if clients is not None else discover_clients():
        path = Path(client["path"]).expanduser()
        key = client["key"]
        current = _safe_json(path)
        servers = _get_nested(current, key)
        previous = servers.get("auren")
        if previous == server:
            outcomes.append({"client": client["name"], "path": str(path), "status": "already_configured"})
            continue
        preview = dict(current)
        preview_servers = _get_nested(preview, key)
        preview_servers["auren"] = server
        if not apply:
            outcomes.append({"client": client["name"], "path": str(path), "status": "preview",
                             "existing_auren_entry": previous, "proposed_entry": server})
            continue
        backup = _atomic_config_write(
            path, (json.dumps(preview, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        outcomes.append({"client": client["name"], "path": str(path), "status": "configured",
                         "backup": str(backup)})
    return {"applied": apply, "clients": outcomes}


def doctor(project_root: str | Path = ".") -> dict[str, Any]:
    """Report runtime prerequisites without claiming missing providers are ready."""
    root = Path(project_root).expanduser().resolve()
    report: dict[str, Any] = {
        "project": str(root),
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
        "local_http_dependency": {"available": False, "package": "uvicorn"},
        "providers": [],
        "runtime_factory": {"required_by_current_local_host": True, "status": "not_configured"},
        "next_action": "The built-in managed runtime composition root is not yet available in this release.",
    }
    try:
        import uvicorn  # noqa: F401
        report["local_http_dependency"]["available"] = True
    except ImportError:
        pass
    try:
        from .provider_fabric import ProviderFabric
        report["providers"] = [
            {"id": name, "command": item.command, "version": item.version,
             "capabilities": list(item.capabilities), "status": "detected_not_live_validated"}
            for name, item in sorted(ProviderFabric().discover().items())
        ]
    except Exception as exc:
        report["provider_discovery_error"] = type(exc).__name__
    report["repository"] = inspect_repository(root)
    return report
