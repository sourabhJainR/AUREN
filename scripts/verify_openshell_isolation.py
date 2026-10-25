#!/usr/bin/env python3
"""Run real, fail-closed Linux OpenShell isolation probes.

This is an acceptance probe, not a mock/unit test. It refuses to report success
unless the host can reach the selected network target before sandboxing, then
the same target is unreachable from inside the sandbox, an outside-host sentinel
cannot be changed, and a host-only process marker is not visible in the sandbox
PID namespace. Run only with an explicit OpenShell policy supplied by an operator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from portable.openshell_sandbox import (
    OpenShellExecutionRequest,
    OpenShellSandboxBackend,
    OpenShellUnavailableError,
)


_PROBE = r'''
import hashlib, os, socket, sys
sentinel, network_host, network_port, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
try:
    with open(sentinel, "a", encoding="utf-8") as stream:
        stream.write("SANDBOX_ESCAPE\n")
    print("FAIL: host sentinel path was writable from sandbox", file=sys.stderr)
    raise SystemExit(31)
except OSError:
    pass
try:
    with open("/etc/auren-isolation-probe", "w", encoding="utf-8") as stream:
        stream.write("escape")
    os.unlink("/etc/auren-isolation-probe")
    print("FAIL: outside-workspace system path was writable", file=sys.stderr)
    raise SystemExit(32)
except OSError:
    pass
try:
    with socket.create_connection((network_host, network_port), timeout=2.0):
        print("FAIL: network egress probe succeeded inside sandbox", file=sys.stderr)
        raise SystemExit(33)
except OSError:
    pass
marker = hashlib.sha256(seed.encode("utf-8")).hexdigest()
proc = "/proc"
if not os.path.isdir(proc):
    print("FAIL: /proc unavailable; process isolation not verified", file=sys.stderr)
    raise SystemExit(34)
for name in os.listdir(proc):
    if not name.isdigit() or int(name) == os.getpid():
        continue
    try:
        raw = open(os.path.join(proc, name, "cmdline"), "rb").read(65536)
    except OSError:
        continue
    if marker.encode("ascii") in raw:
        print("FAIL: host-only process marker visible in sandbox PID namespace", file=sys.stderr)
        raise SystemExit(35)
print("PASS: filesystem, egress, and PID-namespace probes denied")
'''


def _host_can_reach(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, help="explicit OpenShell policy YAML path")
    parser.add_argument("--network-host", default="1.1.1.1")
    parser.add_argument("--network-port", type=int, default=53)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    if platform.system() != "Linux":
        print("BLOCKED: this probe verifies Linux /proc PID isolation only", file=sys.stderr)
        return 2
    policy = Path(args.policy).expanduser().resolve()
    if not policy.is_file() or not policy.read_bytes().strip():
        print("BLOCKED: explicit non-empty policy file required", file=sys.stderr)
        return 2
    if not _host_can_reach(args.network_host, args.network_port):
        print(
            "BLOCKED: host cannot reach the network probe target; egress denial would be inconclusive",
            file=sys.stderr,
        )
        return 2

    backend = OpenShellSandboxBackend()
    try:
        with tempfile.TemporaryDirectory(prefix="auren-isolation-acceptance-") as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "probe.txt").write_text("curated workspace\n", encoding="utf-8")
            sentinel = root / "host-only-sentinel.txt"
            sentinel.write_text("host sentinel unchanged\n", encoding="utf-8")
            original_sentinel = sentinel.read_bytes()
            seed = os.urandom(32).hex()
            host_process = subprocess.Popen(
                [sys.executable, "-c", "import sys,time; print(sys.argv[1]); time.sleep(90)", hashlib.sha256(seed.encode()).hexdigest()],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            try:
                result = backend.execute(OpenShellExecutionRequest(
                    name_prefix="auren-isolation",
                    workspace=str(workspace),
                    policy_file=str(policy),
                    command=(
                        "python3", "-c", _PROBE, str(sentinel),
                        args.network_host, str(args.network_port), seed,
                    ),
                    timeout_seconds=args.timeout,
                ))
            finally:
                host_process.terminate()
                try:
                    host_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    host_process.kill()
                    host_process.wait(timeout=3)

            sentinel_unchanged = sentinel.read_bytes() == original_sentinel
            evidence = result.as_evidence()
            evidence["probes"] = {
                "host_network_precondition": "passed",
                "host_sentinel_unchanged": sentinel_unchanged,
                "sandbox_exit_code": result.exit_code,
                "pid_namespace_probe": "passed" if result.exit_code == 0 else "not_proven",
            }
            print(json.dumps(evidence, sort_keys=True, indent=2))
            if not sentinel_unchanged:
                print("FAIL: sandbox modified host sentinel", file=sys.stderr)
                return 1
            if not result.passed:
                print("FAIL: OpenShell execution or isolation probes failed", file=sys.stderr)
                return 1
            return 0
    except (OpenShellUnavailableError, OSError, ValueError) as exc:
        print(f"BLOCKED: isolation probe could not complete: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
