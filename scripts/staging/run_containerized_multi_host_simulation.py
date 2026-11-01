"""Run three container-isolated AUREN instances against one PostgreSQL service."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def run(args, *, capture=True, timeout=120):
    try:
        return subprocess.run(
            args, check=True, text=True, capture_output=capture, timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"command failed ({exc.returncode}): {args!r}; "
            f"stdout={exc.stdout!r}; stderr={exc.stderr!r}"
        ) from exc


def main() -> int:
    if shutil.which("docker") is None:
        raise SystemExit("Docker is required for the containerized staging rehearsal")
    stamp = lambda: datetime.now(timezone.utc).isoformat()
    token = uuid4().hex[:12]
    network = "auren-host-sim-" + token
    database = "auren-host-sim-db-" + token
    image = "auren-runtime-host-sim:" + token
    namespace = "container-staging-" + token
    password = "staging-only-" + uuid4().hex
    dsn = f"postgresql://auren_stage:{password}@{database}:5432/auren_stage"
    names = [f"auren-runtime-{token}-{name}" for name in ("a", "b", "c")]
    owners = [f"container-host-{name}-{token}" for name in ("a", "b", "c")]
    work_key = "container-staging:" + token + ":same-logical-work"
    state = Path(tempfile.mkdtemp(prefix="auren-container-staging-"))
    report = {
        "schema": "auren-containerized-host-simulation-v1",
        "started_at": stamp(),
        "namespace": namespace,
        "policy_version": "container-staging-v1",
        "simulation_boundary": {
            "runtime_instances": 3,
            "independent_containers": True,
            "independent_network_namespaces": True,
            "independent_state_roots": True,
            "shared_postgresql_service": True,
            "physical_hosts": 1,
            "physical_multi_host_claim": False,
        },
        "instances": [],
    }
    created = []
    try:
        run(["docker", "network", "create", network])
        run([
            "docker", "run", "-d", "--name", database, "--network", network,
            "--network-alias", database,
            "-e", "POSTGRES_USER=auren_stage",
            "-e", f"POSTGRES_PASSWORD={password}",
            "-e", "POSTGRES_DB=auren_stage",
            "postgres:16",
        ])
        created.append(database)
        ready = False
        for _ in range(60):
            probe = subprocess.run(
                ["docker", "exec", database, "pg_isready", "-U", "auren_stage", "-d", "auren_stage"],
                text=True, capture_output=True,
            )
            if probe.returncode == 0:
                ready = True
                break
            time.sleep(1)
        if not ready:
            raise RuntimeError("containerized PostgreSQL service did not become ready")

        run(["docker", "build", "-f", "scripts/staging/Dockerfile", "-t", image, "."])
        run([
            "docker", "run", "--rm", "--network", network, image,
            "python", "scripts/staging/multi_instance_container_worker.py",
            "--dsn", dsn, "--namespace", namespace, "--policy", "container-staging-v1",
            "--owner", "schema-provisioner", "--root", "/tmp/provision",
            "--work-key", "provision-only", "--provision-only",
        ])

        gate = state / "start"
        for index, (name, owner) in enumerate(zip(names, owners)):
            root = f"/state/root-{index}"
            run([
                "docker", "run", "-d", "--name", name, "--network", network,
                "-v", f"{state}:/state",
                image,
                "python", "scripts/staging/multi_instance_container_worker.py",
                "--dsn", dsn, "--namespace", namespace, "--policy", "container-staging-v1",
                "--owner", owner, "--root", root, "--work-key", work_key,
                "--wait-for-file", "/state/start",
            ])
            created.append(name)
        # All three containers are started before the shared gate opens.
        # Capture network identities while they are attached and waiting;
        # Docker detaches exited containers from the bridge.
        time.sleep(2)
        container_meta = {}
        for name in names:
            inspected = json.loads(run(["docker", "inspect", name]).stdout)[0]
            container_meta[name] = {
                "container_id": inspected["Id"],
                "network_ip": inspected["NetworkSettings"]["Networks"][network]["IPAddress"],
            }
        network_ips = [item["network_ip"] for item in container_meta.values()]
        if len(set(network_ips)) != 3 or any(not ip for ip in network_ips):
            raise AssertionError(f"runtime containers do not have three distinct network addresses: {network_ips}")
        gate.write_text("go\\n", encoding="utf-8")

        accepted = []
        rejected = []
        for name, owner in zip(names, owners):
            exit_code = int(run(["docker", "wait", name]).stdout.strip())
            logs = run(["docker", "logs", name]).stdout.strip()
            try:
                result = json.loads(logs.splitlines()[-1])
            except (IndexError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"{name} did not emit a valid JSON result: {logs}") from exc
            instance = {
                "container_name": name,
                "container_id": container_meta[name]["container_id"],
                "network_ip": container_meta[name]["network_ip"],
                "runtime_owner": owner,
                "exit_code": exit_code,
                "result": result,
            }
            report["instances"].append(instance)
            if exit_code == 0 and result.get("outcome") == "accepted":
                accepted.append(result)
            elif exit_code == 2 and result.get("outcome") == "duplicate_rejected":
                rejected.append(result)
            else:
                raise RuntimeError(f"unexpected runtime result for {name}: {instance}")

        if len(accepted) != 1 or len(rejected) != 2:
            raise AssertionError(
                f"expected one accepted and two duplicate-rejected submissions; got {report['instances']}"
            )
        if len({item["task_id"] for item in accepted}) != 1:
            raise AssertionError("winning task identity was not unique")
        report["result"] = "passed"
        report["accepted_count"] = len(accepted)
        report["duplicate_rejection_count"] = len(rejected)
        report["canonical_task_id"] = accepted[0]["task_id"]
    finally:
        for name in reversed(created):
            subprocess.run(["docker", "rm", "-f", name], text=True, capture_output=True)
        subprocess.run(["docker", "network", "rm", network], text=True, capture_output=True)
        shutil.rmtree(state, ignore_errors=True)

    report["completed_at"] = stamp()
    report["runner"] = {
        "hostname": platform.node(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        ).stdout.strip(),
    }
    target = Path(os.environ.get(
        "AUREN_CONTAINER_STAGING_REPORT_PATH",
        "artifacts/containerized-multi-host-simulation.json",
    ))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
