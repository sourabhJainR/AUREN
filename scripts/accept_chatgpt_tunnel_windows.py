"""Live Windows lifecycle acceptance for the AUREN Secure MCP Tunnel.

Requires a real OpenAI Platform tunnel, tunnel-client binary, and
CONTROL_PLANE_API_KEY in the environment. This test starts a real local AUREN
host, kills the tunnel client once, verifies bounded recovery, then sends a
console break and verifies child-process cleanup. It does not verify ChatGPT UI.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _children(parent_pid: int) -> list[int]:
    script = (
        "$p=Get-CimInstance Win32_Process -Filter 'ParentProcessId = "
        + str(parent_pid)
        + "'; $p | Where-Object { $_.Name -like '*tunnel-client*' -or "
          "$_.CommandLine -like '*tunnel-client*' } | "
          "Select-Object -ExpandProperty ProcessId"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("could not inspect tunnel-client child process")
    return [int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()]


def _wait_until(predicate, timeout: float, description: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for {description}")


def _ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=1) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run live Windows AUREN tunnel lifecycle acceptance.")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--tunnel-id", required=True)
    parser.add_argument("--tunnel-client", default="tunnel-client")
    parser.add_argument("--config", type=Path, default=Path.home() / ".auren" / "mcp" / "chatgpt-tunnel.yaml")
    parser.add_argument("--mcp-port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)

    if os.name != "nt":
        print("This acceptance test must run on Windows.", file=sys.stderr)
        return 2
    if not os.environ.get("CONTROL_PLANE_API_KEY"):
        print("CONTROL_PLANE_API_KEY must be set in the protected environment.", file=sys.stderr)
        return 2
    if not args.project_root.is_dir():
        print("project root must be an existing directory.", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable, str(repo_root / "auren_cli.py"), "chatgpt-tunnel", "run",
        "--project-root", str(args.project_root.resolve()),
        "--tunnel-id", args.tunnel_id,
        "--tunnel-client", args.tunnel_client,
        "--config", str(args.config.expanduser()),
        "--mcp-port", str(args.mcp_port),
        "--max-restarts", "3",
    ]
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="auren-tunnel-accept-") as temp:
            log_path = Path(temp) / "launcher.log"
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command, cwd=repo_root, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                _wait_until(lambda: _ready(args.mcp_port), args.timeout, "local AUREN MCP readiness")
                first_pid = _wait_until(
                    lambda: next(iter(_children(process.pid)), None),
                    args.timeout, "initial tunnel-client process",
                )
                doctor = subprocess.run(
                    [args.tunnel_client, "doctor", "--config", str(args.config.expanduser()), "--explain"],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                if doctor.returncode != 0:
                    raise RuntimeError("tunnel-client doctor failed after startup")

                subprocess.run(["taskkill", "/PID", str(first_pid), "/T", "/F"],
                               capture_output=True, text=True, timeout=10, check=False)
                second_pid = _wait_until(
                    lambda: next((pid for pid in _children(process.pid) if pid != first_pid), None),
                    args.timeout, "bounded tunnel-client recovery after forced interruption",
                )
                if second_pid == first_pid:
                    raise RuntimeError("tunnel-client process was not replaced after interruption")
                doctor = subprocess.run(
                    [args.tunnel_client, "doctor", "--config", str(args.config.expanduser()), "--explain"],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                if doctor.returncode != 0:
                    raise RuntimeError("tunnel-client doctor failed after recovery")

                process.send_signal(signal.CTRL_BREAK_EVENT)
                exit_code = process.wait(timeout=20)
                if exit_code not in (0, 130):
                    raise RuntimeError(f"AUREN tunnel launcher exited unexpectedly with code {exit_code}")
                _wait_until(lambda: not _children(process.pid), 10, "tunnel-client shutdown")
                _wait_until(lambda: not _ready(args.mcp_port), 10, "local AUREN host shutdown")
                print("PASS: local startup, tunnel interruption, bounded recovery and process cleanup")
                print("NOTE: ChatGPT Desktop/Web UI discovery is a separate manual acceptance gate.")
                return 0
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if process is not None and process.poll() is None:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                process.wait(timeout=10)
            except Exception:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
