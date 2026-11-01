"""Single AUREN runtime used by the containerized host simulation."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone


def stamp():
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--work-key", required=True)
    parser.add_argument("--wait-for-file")
    parser.add_argument("--provision-only", action="store_true")
    args = parser.parse_args()

    import psycopg
    from portable.execution_controller import ExecutionController
    from portable.postgres_admission_coordinator import (
        DuplicateWorkKeySubmission, PostgreSQLAdmissionCoordinator,
    )
    from tests.test_execution_controller import durable_handler

    coordinator = PostgreSQLAdmissionCoordinator(
        lambda: psycopg.connect(args.dsn),
        namespace=args.namespace,
        policy_version=args.policy,
        provision=args.provision_only,
    )
    if args.provision_only:
        print(json.dumps({"outcome": "provisioned", "namespace": args.namespace}))
        return 0

    if args.wait_for_file:
        deadline = time.monotonic() + 60
        while not os.path.exists(args.wait_for_file):
            if time.monotonic() >= deadline:
                raise TimeoutError("container start gate was not released")
            time.sleep(0.05)

    controller = ExecutionController(
        args.root,
        max_pending_tasks=8,
        deployment_admission_coordinator=coordinator,
        deployment_instance_id=args.owner,
    )
    controller.register_handler(
        "tests.test_execution_controller.durable_handler",
        durable_handler,
        execution_class="read_only",
    )
    try:
        task = controller.submit(
            "tests.test_execution_controller.durable_handler",
            {"id": "same-container-race-payload"},
            goal="containerized multi-host duplicate submission rehearsal",
            work_key=args.work_key,
        )
        print(json.dumps({
            "outcome": "accepted",
            "owner": args.owner,
            "task_id": task.id,
            "root": args.root,
            "container_hostname": platform.node(),
            "pid": os.getpid(),
            "timestamp": stamp(),
        }, sort_keys=True))
        return 0
    except DuplicateWorkKeySubmission as exc:
        print(json.dumps({
            "outcome": "duplicate_rejected",
            "owner": args.owner,
            "error_type": type(exc).__name__,
            "container_hostname": platform.node(),
            "pid": os.getpid(),
            "timestamp": stamp(),
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
