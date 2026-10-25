#!/usr/bin/env python3
"""Verify the actual Docker evaluator boundary with independent OS-level probes.

Requires Linux, Docker, and an immutable digest-pinned image containing Python.
A passing result is scoped to the recorded image digest and Docker runtime policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import select
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portable.docker_isolated_evaluator import DockerEvaluatorPolicy, DockerIsolatedEvaluatorExecutor
from portable.external_evaluator_gateway import ExternalEvaluatorCommand


_PROBE = r'''
import hashlib, json, os, socket, sys, time
sentinel, sentinel_value, host, port, seed = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
checks = {}
try:
    with open(sentinel, "r", encoding="utf-8") as stream:
        if stream.read().strip() == sentinel_value:
            raise SystemExit("FAIL: host sentinel content is visible in container")
except OSError:
    pass
checks["host_sentinel_not_visible"] = True
try:
    with open("/etc/auren-isolation-probe", "w", encoding="utf-8") as stream:
        stream.write("escape")
    raise SystemExit("FAIL: read-only root filesystem accepted a write")
except OSError:
    checks["rootfs_write_denied"] = True
try:
    path = "/tmp/auren-isolation-write-probe"
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("tmpfs-ok")
    with open(path, "r", encoding="utf-8") as stream:
        checks["sandbox_tmpfs_writable"] = stream.read() == "tmpfs-ok"
except OSError:
    checks["sandbox_tmpfs_writable"] = False
if not checks["sandbox_tmpfs_writable"]:
    raise SystemExit("FAIL: writable sandbox tmpfs unavailable")
try:
    with socket.create_connection((host, port), timeout=2.0):
        raise SystemExit("FAIL: network-disabled container reached host-tested HTTPS endpoint")
except OSError:
    checks["network_egress_denied"] = True
marker = hashlib.sha256(seed.encode("utf-8")).hexdigest().encode("ascii")
if not os.path.isdir("/proc"):
    raise SystemExit("FAIL: /proc unavailable; PID namespace cannot be verified")
for pid in os.listdir("/proc"):
    if not pid.isdigit() or int(pid) == os.getpid():
        continue
    try:
        with open(os.path.join("/proc", pid, "cmdline"), "rb") as stream:
            command_line = stream.read(65536)
    except OSError:
        continue
    if marker in command_line:
        raise SystemExit("FAIL: host-only process marker visible in container PID namespace")
checks["host_process_not_visible"] = True
print("AUREN_ISOLATION_RESULT=" + json.dumps(checks, sort_keys=True), flush=True)
# Keep the container alive briefly so the host can inspect its actual runtime config.
time.sleep(5)
'''


def _host_network_precondition() -> bool:
    try:
        with socket.create_connection(("api.github.com", 443), timeout=3.0):
            return True
    except OSError:
        return False


def _runtime_policy(container: dict) -> dict:
    host = container.get("HostConfig") or {}
    config = container.get("Config") or {}
    tmpfs = host.get("Tmpfs") or {}
    security_options = host.get("SecurityOpt") or []
    return {
        "network_mode_none": host.get("NetworkMode") == "none",
        "read_only_rootfs": host.get("ReadonlyRootfs") is True,
        "all_capabilities_dropped": "ALL" in (host.get("CapDrop") or []),
        "no_new_privileges": any("no-new-privileges" in item for item in security_options),
        "non_root_user": config.get("User") == "65532:65532",
        "bounded_pids": isinstance(host.get("PidsLimit"), int) and 0 < host["PidsLimit"] <= 64,
        "bounded_memory": isinstance(host.get("Memory"), int) and 0 < host["Memory"] <= 256 * 1024 * 1024,
        "bounded_cpu": isinstance(host.get("NanoCpus"), int) and 0 < host["NanoCpus"] <= 1_000_000_000,
        "restricted_tmpfs": "/tmp" in tmpfs and all(
            flag in tmpfs["/tmp"] for flag in ("noexec", "nosuid", "nodev")
        ),
        "no_host_mounts": not container.get("Mounts"),
        "private_pid_namespace": not host.get("PidMode") or host.get("PidMode") == "private",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="trusted immutable image reference: name@sha256:<64 hex>")
    parser.add_argument("--python", default="python3", help="Python executable inside the pinned image")
    args = parser.parse_args()

    if platform.system() != "Linux":
        print("BLOCKED: Docker OS isolation acceptance requires Linux", file=sys.stderr)
        return 2
    if not _host_network_precondition():
        print("BLOCKED: host cannot reach api.github.com:443; network denial would be inconclusive", file=sys.stderr)
        return 2

    try:
        policy = DockerEvaluatorPolicy(
            image=args.image, uid=65532, gid=65532, memory_limit="256m",
            cpu_limit="1.0", pids_limit=64, tmpfs_size="16m",
        )
        executor = DockerIsolatedEvaluatorExecutor(policy)
        if not executor.production_ready:
            print("BLOCKED: Docker daemon or exact pinned image digest is not verified", file=sys.stderr)
            return 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"BLOCKED: isolation runtime policy invalid: {type(exc).__name__}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="auren-docker-isolation-") as tmp:
        root = Path(tmp)
        sentinel = root / "host-only-sentinel.txt"
        seed = os.urandom(32).hex()
        sentinel_value = "AUREN_HOST_SENTINEL:" + seed
        sentinel.write_text(sentinel_value + "\n", encoding="utf-8")
        original_sentinel = sentinel.read_bytes()
        marker = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        host_process = subprocess.Popen(
            [sys.executable, "-c", "import sys,time; time.sleep(90)", marker],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True,
        )
        attached = None
        checks: dict[str, bool] = {}
        container_policy: dict = {}
        try:
            command = ExternalEvaluatorCommand(
                argv=(
                    args.python, "-c", _PROBE, str(sentinel), sentinel_value,
                    "api.github.com", "443", seed,
                ),
                timeout_seconds=20.0,
                max_output_bytes=65536,
            )
            attached = executor.launch(command, environment={})
            if attached.stdout is None:
                print("FAIL: Docker evaluator stdout is unavailable", file=sys.stderr)
                return 1
            if not select.select([attached.stdout], [], [], 8.0)[0]:
                print("FAIL: isolation probe did not emit evidence within the bounded wait", file=sys.stderr)
                return 1
            line = attached.stdout.readline(65537).decode("utf-8", errors="replace").strip()
            prefix = "AUREN_ISOLATION_RESULT="
            if not line.startswith(prefix):
                print("FAIL: container did not return a valid isolation receipt", file=sys.stderr)
                return 1
            checks = json.loads(line[len(prefix):])

            container_id = None
            for _ in range(50):
                container_id = attached.container_id
                if container_id:
                    break
                time.sleep(0.1)
            if not container_id:
                print("FAIL: validated Docker container ID is unavailable", file=sys.stderr)
                return 1
            inspect = subprocess.run(
                [executor.docker_binary, "inspect", "--format", "{{json .}}", container_id],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=5, check=False, text=True,
            )
            if inspect.returncode != 0:
                print("FAIL: live Docker runtime configuration could not be inspected", file=sys.stderr)
                return 1
            container_policy = _runtime_policy(json.loads(inspect.stdout))
            return_code = attached.wait(timeout=15)
            if return_code != 0:
                print("FAIL: isolated probe exited unsuccessfully", file=sys.stderr)
                return 1
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            print(f"FAIL: isolation probe did not complete: {type(exc).__name__}", file=sys.stderr)
            return 1
        finally:
            if attached is not None:
                attached.terminate_isolated()
            host_process.terminate()
            try:
                host_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                host_process.kill()
                host_process.wait(timeout=3)

        sentinel_unchanged = sentinel.read_bytes() == original_sentinel
        checks["host_sentinel_unchanged"] = sentinel_unchanged
        checks["host_process_not_visible"] = checks.get("host_process_not_visible") is True
        policy_passed = bool(container_policy) and all(container_policy.values())
        probes_passed = bool(checks) and all(checks.values())
        evidence = {
            "image_digest": args.image,
            "runtime": "DockerIsolatedEvaluatorExecutor",
            "host_network_precondition": "passed",
            "container_policy": container_policy,
            "probes": checks,
            "policy_passed": policy_passed,
            "probes_passed": probes_passed,
        }
        print(json.dumps(evidence, sort_keys=True, indent=2))
        if not policy_passed or not probes_passed:
            print("FAIL: actual OS isolation contract was not proven", file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
