"""Run AUREN's local observation console and authenticated MCP endpoint."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import socket
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .identity_compat import canonical_auren_home, canonical_runtime_home
from .remote_mcp import AurenMCPASGI, AurenMCPService, StaticBearerTokenVerifier

_LOCAL_SCOPES = (
    "auren:tasks:read",
    "auren:tasks:write",
    "auren:tasks:cancel",
    "auren:tasks:resume",
    "auren:evidence:read",
    "auren:approvals:read",
    "auren:approvals:decide",
    "auren:admin",
)


def ensure_local_observability_store() -> Path:
    """Create an empty, private trace journal so first-run dashboard readiness is truthful."""
    root = canonical_runtime_home()
    directory = root / "observability"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise RuntimeError("local observability directory must not be a symlink")
    if os.name != "nt":
        directory.chmod(0o700)
    path = directory / "traces.jsonl"
    if path.is_symlink():
        raise RuntimeError("local observability trace journal must not be a symlink")
    if path.exists():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("local observability trace journal must be a regular non-symlink file")
        if os.name != "nt":
            path.chmod(0o600)
        return path
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("local observability trace journal must be a regular file")
    else:
        os.close(descriptor)
    return path


def local_token_path() -> Path:
    """Return the per-user token path shared by the local host and stdio proxy."""
    return canonical_auren_home() / "mcp" / "local-operator.token"


def _read_token_file(path: Path) -> str:
    if path.is_symlink():
        raise RuntimeError("local MCP token path must be a regular, non-symlink file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {getattr(os, "ELOOP", 40), getattr(os, "EMLINK", 31)}:
            raise RuntimeError("local MCP token path must be a regular, non-symlink file") from exc
        raise
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("local MCP token path must be a regular, non-symlink file")
        if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PermissionError("local MCP token file is readable by group or others; set permissions to 600")
        token = stream.read(4097).strip()
    if len(token) < 32 or len(token) > 4096 or "\n" in token or "\r" in token:
        raise RuntimeError("local MCP token file is malformed; remove it and restart the local host to rotate it")
    return token

def load_or_create_local_token(path: Path | None = None) -> tuple[str, Path]:
    """Load a stable local credential or create it with owner-only permissions."""
    target = (path or local_token_path()).expanduser()
    parent = target.parent
    if parent.is_symlink():
        raise RuntimeError("local MCP token directory must not be a symlink")
    parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        parent.chmod(0o700)
    if target.exists() or target.is_symlink():
        return _read_token_file(target), target
    token = secrets.token_urlsafe(48)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError:
        return _read_token_file(target), target
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(token + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return token, target


def create_local_mcp_service(
    runtime: Any, project_root: Path | str, port: int, *, token_path: Path | None = None,
) -> tuple[AurenMCPService, Path]:
    """Create a loopback-only MCP service with a per-user local bearer token."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("project root must be an existing directory")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("local MCP port must be a bound TCP port")
    token, credential_path = load_or_create_local_token(token_path)
    verifier = StaticBearerTokenVerifier([{
        "token": token,
        "subject": "local-operator",
        "issuer": "auren-local",
        "roles": ["local-operator"],
        "projects": [str(root)],
        "scopes": list(_LOCAL_SCOPES),
    }])
    state_root = canonical_auren_home() / "mcp"
    state_root.mkdir(parents=True, exist_ok=True)
    service = AurenMCPService(
        runtime,
        project_root=root,
        token_verifier=verifier,
        resource_url=f"http://127.0.0.1:{port}/mcp",
        allowed_hosts=(f"127.0.0.1:{port}",),
        allowed_origins=(),
        require_https=False,
        control_store_path=state_root / "local-operator-control.sqlite3",
    )
    return service, credential_path


def resolve_runtime_factory(project_root: Path | str, requested: str | None = None) -> str:
    """Resolve an explicit factory or the conventional project-local factory."""
    root = Path(project_root).expanduser().resolve()
    configured = (requested or os.environ.get("AUREN_MCP_RUNTIME_FACTORY", "")).strip()
    if configured:
        return configured
    if (root / "auren_runtime_factory.py").is_file():
        return "auren_runtime_factory:create_runtime"
    raise RuntimeError(
        "No AUREN runtime factory was found. Add auren_runtime_factory.py with "
        "create_runtime(*, project_root), or pass --runtime-factory module:function. "
        "The factory must return a configured runtime with a reviewed, non-empty agent graph."
    )


def _bound_loopback_socket(port: int) -> socket.socket:
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535; use 0 for automatic selection")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


def serve_local(
    project_root: Path | str = ".",
    *,
    runtime_factory: str | None = None,
    dashboard_port: int = 0,
    mcp_port: int = 0,
) -> int:
    """Start the read-only console and full operator MCP service on free loopback ports."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit("AUREN project root must be an existing directory")
    try:
        factory_path = resolve_runtime_factory(root, runtime_factory)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install the local HTTP runtime with: python -m pip install 'uvicorn[standard]'") from exc

    from .dashboard_server import create_server
    from .remote_mcp_server import _load_runtime

    if str(root) in sys.path:
        sys.path.remove(str(root))
    sys.path.insert(0, str(root))
    runtime = _load_runtime(factory_path, root)
    ensure_local_observability_store()
    dashboard = None
    mcp_socket = None
    service = None
    dashboard_thread = None
    mcp_thread = None
    stop = threading.Event()
    shutdown_requested = threading.Event()
    failures: list[tuple[str, BaseException]] = []
    previous_handlers = {}

    def request_shutdown(_signum, _frame) -> None:
        shutdown_requested.set()
        stop.set()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_shutdown)

    try:
        dashboard = create_server(root, "127.0.0.1", dashboard_port)
        mcp_socket = _bound_loopback_socket(mcp_port)
        actual_mcp_port = int(mcp_socket.getsockname()[1])
        service, credential_path = create_local_mcp_service(
            runtime, root, actual_mcp_port,
        )
        app = AurenMCPASGI(service)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=actual_mcp_port,
            log_level="warning",
            access_log=False,
            proxy_headers=False,
            server_header=False,
            date_header=False,
            lifespan="on",
            timeout_graceful_shutdown=10,
        )
        mcp_server = uvicorn.Server(config)

        def run_dashboard() -> None:
            try:
                dashboard.serve_forever(poll_interval=0.2)
            except BaseException as exc:
                failures.append(("dashboard", exc))
            finally:
                if not shutdown_requested.is_set() and not failures:
                    failures.append(("dashboard", RuntimeError("dashboard server stopped without a shutdown request")))
                stop.set()

        def run_mcp() -> None:
            try:
                mcp_server.run(sockets=[mcp_socket])
            except BaseException as exc:
                failures.append(("MCP", exc))
            finally:
                if not shutdown_requested.is_set() and not failures:
                    failures.append(("MCP", RuntimeError("MCP server stopped without a shutdown request")))
                stop.set()

        dashboard_thread = threading.Thread(
            target=run_dashboard, name="auren-local-dashboard", daemon=True,
        )
        mcp_thread = threading.Thread(
            target=run_mcp, name="auren-local-mcp", daemon=True,
        )
        dashboard_thread.start()
        mcp_thread.start()

        dashboard_url = f"http://127.0.0.1:{dashboard.server_address[1]}/control-center"
        dashboard_health_url = f"http://127.0.0.1:{dashboard.server_address[1]}/api/health"
        mcp_url = f"http://127.0.0.1:{actual_mcp_port}/mcp"
        mcp_health_url = f"http://127.0.0.1:{actual_mcp_port}/readyz"

        def endpoint_ready(url: str) -> bool:
            try:
                from urllib.request import urlopen
                with urlopen(url, timeout=0.5) as response:
                    if response.status != 200:
                        return False
                    if url == dashboard_health_url:
                        health = json.loads(response.read(64 * 1024).decode("utf-8"))
                        return isinstance(health, dict) and health.get("readiness") == "ready"
                    return True
            except Exception:
                return False

        startup_deadline = time.monotonic() + 15.0
        while time.monotonic() < startup_deadline:
            if stop.is_set() or not dashboard_thread.is_alive() or not mcp_thread.is_alive():
                break
            if (
                mcp_server.started
                and endpoint_ready(dashboard_health_url)
                and endpoint_ready(mcp_health_url)
            ):
                break
            time.sleep(0.05)
        if not (
            mcp_server.started
            and endpoint_ready(dashboard_health_url)
            and endpoint_ready(mcp_health_url)
        ):
            detail = f": {failures[0][1]}" if failures else ""
            raise RuntimeError("local dashboard and MCP endpoints did not both become ready within 15 seconds" + detail)
        print("AUREN local services are ready.", flush=True)
        print(f"Observation console: {dashboard_url}", flush=True)
        print(f"Console health: http://127.0.0.1:{dashboard.server_address[1]}/api/health", flush=True)
        print(f"MCP endpoint: {mcp_url}", flush=True)
        print(f"MCP credential file: {credential_path}", flush=True)
        print("MCP clients without HTTP bearer-header support: use the AUREN stdio proxy.", flush=True)
        print("Keep this process running. Press Ctrl+C to stop both services.", flush=True)

        while not stop.wait(0.25):
            if not dashboard_thread.is_alive() or not mcp_thread.is_alive():
                stop.set()
                break
        if failures and not shutdown_requested.is_set():
            raise RuntimeError(f"{failures[0][0]} service stopped unexpectedly: {failures[0][1]}")
        return 0
    except KeyboardInterrupt:
        shutdown_requested.set()
        return 0
    finally:
        stop.set()
        if "mcp_server" in locals():
            mcp_server.should_exit = True
        if dashboard is not None and dashboard_thread is not None and dashboard_thread.is_alive():
            try:
                dashboard.shutdown()
            except Exception:
                pass
        if dashboard_thread is not None:
            dashboard_thread.join(timeout=5)
        if mcp_thread is not None:
            # Uvicorn owns the MCP ASGI lifespan and closes the service there.
            # Wait for that close before a direct fallback close; concurrent
            # close calls could race the durable trigger worker.
            mcp_thread.join(timeout=35)
        if dashboard is not None:
            dashboard.server_close()
        if mcp_socket is not None:
            try:
                mcp_socket.close()
            except OSError:
                pass
        if service is not None and (mcp_thread is None or not mcp_thread.is_alive()):
            service.close()
        elif mcp_thread is not None and mcp_thread.is_alive():
            print(
                "AUREN MCP shutdown exceeded 35 seconds; the daemon host thread will be terminated with the process.",
                file=sys.stderr, flush=True,
            )
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the AUREN observation console and local MCP service.",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-factory", default=None)
    parser.add_argument("--dashboard-port", type=int, default=0, help="TCP port; 0 selects a free port")
    parser.add_argument("--mcp-port", type=int, default=0, help="TCP port; 0 selects a free port")
    args = parser.parse_args(argv)
    return serve_local(
        args.project_root,
        runtime_factory=args.runtime_factory,
        dashboard_port=args.dashboard_port,
        mcp_port=args.mcp_port,
    )


if __name__ == "__main__":
    raise SystemExit(main())
