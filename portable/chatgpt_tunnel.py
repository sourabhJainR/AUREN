"""Run AUREN's local MCP host through OpenAI Secure MCP Tunnel.

The tunnel client is an external, operator-installed process. AUREN owns the
local runtime and Guard; OpenAI's tunnel client only transports MCP requests.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from .guarded_network import NetworkPolicyDenied, guarded_loopback_http_get
from .identity_compat import canonical_auren_home


DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp"
DEFAULT_TUNNEL_CONFIG = canonical_auren_home() / "mcp" / "chatgpt-tunnel.yaml"


def validate_tunnel_id(value: str) -> str:
    import re

    if not isinstance(value, str) or not re.fullmatch(
        r"tunnel_(?:[a-z0-9]{32}|[a-z0-9]{4}_[a-z0-9]{32})", value
    ):
        raise ValueError("invalid tunnel ID; copy the complete ID from OpenAI Platform Tunnels")
    return value


def validate_local_mcp_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username
        or parsed.password
        or parsed.port is None
        or not 1 <= parsed.port <= 65535
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("MCP URL must be an explicit IPv4 loopback URL such as http://127.0.0.1:8765/mcp")
    return value


def _yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def render_tunnel_config(tunnel_id: str, token_file: str | Path, mcp_url: str = DEFAULT_MCP_URL) -> str:
    tunnel_id = validate_tunnel_id(tunnel_id)
    mcp_url = validate_local_mcp_url(mcp_url)
    token_source = Path(token_file).expanduser()
    if token_source.is_symlink() or token_source.parent.is_symlink():
        raise ValueError("local MCP token path and parent must not be symlinks")
    if token_source.is_symlink() or not token_source.exists() or not token_source.is_file():
        raise ValueError("local MCP token file must exist and be a regular, non-symlink file before tunnel configuration")
    from .windows_acl import enforce_private_acl
    enforce_private_acl(token_source, directory=False)
    from .local_host import _read_token_file
    _read_token_file(token_source)
    token_path = token_source.resolve()
    token_reference = "file:" + token_path.as_posix()
    return (
        "config_version: 1\n"
        "control_plane:\n"
        "  base_url: https://api.openai.com\n"
        f"  tunnel_id: {_yaml_quote(tunnel_id)}\n"
        "  api_key: env:CONTROL_PLANE_API_KEY\n"
        "mcp:\n"
        "  server_urls:\n"
        "    - channel: main\n"
        f"      url: {_yaml_quote(mcp_url)}\n"
        "  extra_headers:\n"
        f"    Authorization: {_yaml_quote(token_reference)}\n"
        "  startup_wait_timeout: 30s\n"
        "health:\n"
        "  listen_addr: 127.0.0.1:0\n"
    )


def write_tunnel_config(
    tunnel_id: str,
    token_file: str | Path,
    *,
    config_path: str | Path = DEFAULT_TUNNEL_CONFIG,
    mcp_url: str = DEFAULT_MCP_URL,
    overwrite: bool = False,
) -> Path:
    target = Path(config_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("tunnel config path and parent must not be symlinks")
    if target.exists() and not overwrite:
        raise FileExistsError(f"tunnel config already exists; inspect it or pass --overwrite: {target}")
    content = render_tunnel_config(tunnel_id, token_file, mcp_url)
    temporary = target.with_name(target.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise FileExistsError(f"temporary tunnel config path already exists: {temporary}")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            backup = target.with_name(target.name + ".bak")
            if backup.exists() or backup.is_symlink():
                raise FileExistsError(f"backup already exists; move it before overwriting: {backup}")
            target.replace(backup)
        temporary.replace(target)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    if os.name != "nt":
        target.chmod(0o600)
    else:
        from .windows_acl import enforce_private_acl
        enforce_private_acl(target.parent, directory=True)
        enforce_private_acl(target, directory=False)
    return target.resolve()


def _ready(url: str, timeout: float = 0.75) -> bool:
    """Probe only an explicit IPv4 loopback endpoint with bounded response bytes."""
    try:
        status, _body = guarded_loopback_http_get(url, timeout=timeout, max_bytes=1024)
        return status == 200
    except (NetworkPolicyDenied, OSError, TimeoutError, ValueError):
        return False


def _run(args: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="auren chatgpt-tunnel",
        description="Connect the local AUREN MCP service to ChatGPT through OpenAI Secure MCP Tunnel.",
    )
    commands = parser.add_subparsers(dest="action", required=True)

    configure = commands.add_parser("configure", help="Write a secret-free tunnel-client config.")
    configure.add_argument("--tunnel-id", required=True)
    configure.add_argument("--token-file", default=None)
    configure.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    configure.add_argument("--config", default=str(DEFAULT_TUNNEL_CONFIG))
    configure.add_argument("--overwrite", action="store_true")

    run = commands.add_parser("run", help="Start the local AUREN host and tunnel-client in the foreground.")
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("--project-root", help="Existing local GitHub project checkout.")
    target.add_argument("--repository", help="GitHub HTTPS repository to clone into AUREN workspace storage.")
    run.add_argument("--branch", help="Branch to clone; valid only with --repository.")
    run.add_argument("--tunnel-id", required=True)
    run.add_argument("--mcp-port", type=int, default=8765)
    run.add_argument("--dashboard-port", type=int, default=0)
    run.add_argument("--tunnel-client", default="tunnel-client")
    run.add_argument("--config", default=str(DEFAULT_TUNNEL_CONFIG))
    run.add_argument("--overwrite-config", action="store_true")
    run.add_argument("--max-restarts", type=int, default=3, help="Maximum tunnel-client restarts after an unexpected exit (0-10).")

    status = commands.add_parser("status", help="Check local MCP, tunnel client and config readiness.")
    status.add_argument("--config", default=str(DEFAULT_TUNNEL_CONFIG))
    status.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    status.add_argument("--tunnel-client", default="tunnel-client")

    options = parser.parse_args(args)
    try:
        if options.action == "configure":
            from .local_host import local_token_path
            token_file = Path(options.token_file).expanduser() if options.token_file else local_token_path()
            path = write_tunnel_config(
                options.tunnel_id, token_file, config_path=options.config,
                mcp_url=options.mcp_url, overwrite=options.overwrite,
            )
            print(json.dumps({
                "status": "configured",
                "config_path": str(path),
                "token_file": str(token_file),
                "secret_storage": "runtime API key is read from CONTROL_PLANE_API_KEY; no key is written to disk",
                "mcp_target": options.mcp_url,
            }, indent=2, sort_keys=True))
            return 0

        if options.action == "status":
            client = shutil.which(options.tunnel_client) if os.path.sep not in options.tunnel_client else options.tunnel_client
            config_path = Path(options.config).expanduser()
            report = {
                "tunnel_client": "available" if client and Path(client).is_file() else "missing",
                "local_mcp": "ready" if _ready(options.mcp_url.rsplit("/mcp", 1)[0] + "/readyz") else "not_ready",
                "config": "present" if config_path.is_file() and not config_path.is_symlink() else "missing",
                "tunnel_connection": "not_checked",
            }
            if report["tunnel_client"] == "available" and report["config"] == "present":
                try:
                    doctor = subprocess.run(
                        [str(client), "doctor", "--config", str(config_path), "--explain"],
                        capture_output=True, text=True, timeout=10, check=False,
                    )
                    report["tunnel_connection"] = "healthy" if doctor.returncode == 0 else "unhealthy"
                    report["tunnel_diagnostic"] = (doctor.stdout + doctor.stderr)[-2000:]
                except (OSError, subprocess.TimeoutExpired) as exc:
                    report["tunnel_connection"] = "unknown"
                    report["tunnel_diagnostic"] = type(exc).__name__
            report["status"] = (
                "ready"
                if report["tunnel_client"] == "available"
                and report["local_mcp"] == "ready"
                and report["config"] == "present"
                and report["tunnel_connection"] == "healthy"
                else "blocked"
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["status"] == "ready" else 2

        if options.branch and not options.repository:
            parser.error("--branch requires --repository")
        if not os.environ.get("CONTROL_PLANE_API_KEY"):
            raise ValueError("CONTROL_PLANE_API_KEY is not set; supply it through a protected environment variable")
        if not 1 <= options.mcp_port <= 65535:
            raise ValueError("--mcp-port must be between 1 and 65535")
        if not 0 <= options.max_restarts <= 10:
            raise ValueError("--max-restarts must be between 0 and 10")
        client = shutil.which(options.tunnel_client) if os.path.sep not in options.tunnel_client else options.tunnel_client
        if not client or not Path(client).is_file():
            raise FileNotFoundError("tunnel-client was not found; install the supported Windows binary from OpenAI Platform Tunnels and add it to PATH")
        from .local_host import local_token_path
        token_file = local_token_path()
        if options.project_root:
            from .onboarding import initialize_repository, load_project_profile
            project_root = Path(options.project_root).expanduser().resolve()
            if not project_root.is_dir():
                raise ValueError("project root must be an existing local directory")
            project_profile = project_root / ".auren" / "project.json"
            if project_profile.exists() or project_profile.is_symlink():
                _profile_path, profile = load_project_profile(project_root)
                if profile.get("runtime_mode", "single_user_local") != "single_user_local":
                    raise ValueError("ChatGPT local tunnel currently requires single_user_local runtime mode")
            else:
                initialize_repository(project_root, apply=True)
        local_args = [
            sys.executable, str(Path(__file__).resolve().parents[1] / "auren_cli.py"),
            "local", "--mcp-port", str(options.mcp_port),
            "--dashboard-port", str(options.dashboard_port),
        ]
        if options.project_root:
            local_args.extend(["--project-root", str(project_root)])
        else:
            local_args.extend(["--repository", options.repository])
            if options.branch:
                local_args.extend(["--branch", options.branch])
        local_env = os.environ.copy()
        local_env.pop("CONTROL_PLANE_API_KEY", None)
        local_process = subprocess.Popen(local_args, env=local_env)
        tunnel_process = None
        ready_url = f"http://127.0.0.1:{options.mcp_port}/readyz"
        try:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if local_process.poll() is not None:
                    raise RuntimeError(f"AUREN local host exited during startup with code {local_process.returncode}")
                if _ready(ready_url):
                    break
                time.sleep(0.25)
            else:
                raise TimeoutError("AUREN local MCP did not become ready within 30 seconds")
            if not token_file.is_file():
                raise RuntimeError("AUREN local MCP became ready but its operator token file is missing")
            config_target = Path(options.config).expanduser()
            expected_config = render_tunnel_config(
                options.tunnel_id, token_file,
                mcp_url=f"http://127.0.0.1:{options.mcp_port}/mcp",
            )
            if config_target.is_symlink():
                raise ValueError("tunnel config path must not be a symlink")
            if config_target.is_file() and config_target.read_text(encoding="utf-8") == expected_config:
                config_path = config_target.resolve()
            else:
                config_path = write_tunnel_config(
                    options.tunnel_id, token_file, config_path=config_target,
                    mcp_url=f"http://127.0.0.1:{options.mcp_port}/mcp",
                    overwrite=options.overwrite_config,
                )
            print(f"AUREN local MCP ready: http://127.0.0.1:{options.mcp_port}/mcp", flush=True)
            print(f"Tunnel config: {config_path}", flush=True)
            print("Starting OpenAI Secure MCP Tunnel. Keep this process running while using ChatGPT.", flush=True)
            restart_count = 0
            backoff_seconds = 1
            last_doctor_check = 0.0
            tunnel_process = subprocess.Popen([str(client), "run", "--config", str(config_path)])
            while True:
                now = time.monotonic()
                exit_code = tunnel_process.poll()
                if exit_code is not None:
                    if restart_count >= options.max_restarts:
                        raise RuntimeError(
                            f"tunnel client exited with code {exit_code}; restart limit "
                            f"{options.max_restarts} exhausted"
                        )
                    restart_count += 1
                    print(
                        f"Tunnel client exited ({exit_code}); restart "
                        f"{restart_count}/{options.max_restarts} in {backoff_seconds}s.",
                        file=sys.stderr, flush=True,
                    )
                    time.sleep(backoff_seconds)
                    backoff_seconds = min(backoff_seconds * 2, 8)
                    tunnel_process = subprocess.Popen([str(client), "run", "--config", str(config_path)])
                    last_doctor_check = 0.0
                    continue
                if now - last_doctor_check >= 10.0:
                    last_doctor_check = now
                    try:
                        doctor = subprocess.run(
                            [str(client), "doctor", "--config", str(config_path), "--explain"],
                            capture_output=True, text=True, timeout=8, check=False,
                        )
                        if doctor.returncode != 0:
                            print("Tunnel health check is unhealthy; monitoring for process exit before recovery.", file=sys.stderr, flush=True)
                    except (OSError, subprocess.TimeoutExpired):
                        print("Tunnel health check is unavailable; process supervision remains active.", file=sys.stderr, flush=True)
                time.sleep(1.0)
        except KeyboardInterrupt:
            return 130
        finally:
            if tunnel_process is not None and tunnel_process.poll() is None:
                tunnel_process.terminate()
                try:
                    tunnel_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    tunnel_process.kill()
                    tunnel_process.wait()
            if local_process.poll() is None:
                local_process.terminate()
                try:
                    local_process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    local_process.kill()
                    local_process.wait()
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"auren chatgpt-tunnel blocked: {exc}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    return _run(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
