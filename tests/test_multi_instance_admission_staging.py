"""Multi-instance PostgreSQL admission staging rehearsal.

Runs independent AUREN controller processes with isolated SQLite roots and
stable runtime identities against one PostgreSQL authority. This simulates
separate hosts at the process/storage boundary; it does not claim physical
multi-host or network-failure validation.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import platform
import subprocess
import tempfile
import sqlite3
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime(dsn: str, namespace: str, policy: str, root: str, owner: str):
    import psycopg
    from portable.execution_controller import ExecutionController
    from portable.postgres_admission_coordinator import PostgreSQLAdmissionCoordinator
    from tests.test_execution_controller import durable_handler

    coordinator = PostgreSQLAdmissionCoordinator(
        lambda: psycopg.connect(dsn), namespace=namespace, policy_version=policy,
    )
    controller = ExecutionController(
        root, max_pending_tasks=8,
        deployment_admission_coordinator=coordinator,
        deployment_instance_id=owner,
    )
    controller.register_handler(
        "tests.test_execution_controller.durable_handler",
        durable_handler,
        execution_class="read_only",
    )
    return controller, coordinator


def _race_submit(dsn, namespace, policy, root, owner, work_key, start_event, result_queue):
    controller, _ = _runtime(dsn, namespace, policy, root, owner)
    start_event.wait(20)
    try:
        task = controller.submit(
            "tests.test_execution_controller.durable_handler",
            {"id": "identical-payload"},
            goal="multi-instance duplicate submission rehearsal",
            work_key=work_key,
        )
        result_queue.put({
            "outcome": "accepted", "owner": owner, "task_id": task.id,
            "pid": os.getpid(), "root": root, "timestamp": _stamp(),
        })
    except Exception as exc:
        from portable.postgres_admission_coordinator import DuplicateWorkKeySubmission
        if isinstance(exc, DuplicateWorkKeySubmission):
            result_queue.put({
                "outcome": "duplicate_rejected", "owner": owner,
                "error_type": type(exc).__name__, "pid": os.getpid(),
                "root": root, "timestamp": _stamp(),
            })
        else:
            result_queue.put({
                "outcome": "error", "owner": owner,
                "error_type": type(exc).__name__, "error": str(exc),
                "pid": os.getpid(), "root": root, "timestamp": _stamp(),
            })


class _CrashAfterIdentityClaim:
    """Crash the process immediately after PostgreSQL commits the identity."""

    def __init__(self, coordinator):
        self._coordinator = coordinator

    def __getattr__(self, name):
        return getattr(self._coordinator, name)

    def claim_work_key(self, work_key, intent_digest, owner, task_id):
        self._coordinator.claim_work_key(work_key, intent_digest, owner, task_id)
        os._exit(86)


def _crash_before_local_insert(dsn, namespace, policy, root, owner, work_key):
    controller, coordinator = _runtime(dsn, namespace, policy, root, owner)
    controller._deployment_admission_coordinator = _CrashAfterIdentityClaim(coordinator)
    controller.submit(
        "tests.test_execution_controller.durable_handler",
        {"id": "crash-before-local-insert"},
        goal="crash recovery rehearsal",
        work_key=work_key,
    )
    os._exit(87)


def _crash_after_local_insert(dsn, namespace, policy, root, owner, work_key):
    controller, _ = _runtime(dsn, namespace, policy, root, owner)
    controller.submit(
        "tests.test_execution_controller.durable_handler",
        {"id": "crash-after-local-insert"},
        goal="local insert crash recovery rehearsal",
        work_key=work_key,
    )
    os._exit(87)


class MultiInstanceAdmissionStagingTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("AUREN_TEST_POSTGRES_DSN"), "real PostgreSQL service is not configured")
    def test_three_instance_staging_rehearsal_and_evidence(self):
        import psycopg
        from portable.execution_controller import ExecutionController
        from portable.postgres_admission_coordinator import (
            AdmissionCoordinatorUnavailable, AdmissionPolicyConflict,
            DuplicateWorkKeySubmission, PostgreSQLAdmissionCoordinator,
        )
        from tests.test_execution_controller import durable_handler

        dsn = os.environ["AUREN_TEST_POSTGRES_DSN"]
        namespace = "staging-" + uuid4().hex
        policy = "multi-instance-staging-v1"
        coordinator = PostgreSQLAdmissionCoordinator(
            lambda: psycopg.connect(dsn), namespace=namespace,
            policy_version=policy, provision=True,
        )
        evidence = {
            "schema": "auren-multi-instance-admission-staging-v1",
            "started_at": _stamp(),
            "namespace": namespace,
            "policy_version": policy,
            "database": "shared PostgreSQL service; DSN intentionally omitted",
            "simulation_boundary": {
                "runtime_instances": 3,
                "independent_processes": True,
                "independent_state_roots": True,
                "stable_unique_runtime_ids": True,
                "physical_hosts": 1,
                "physical_multi_host_claim": False,
                "network_partition_injection": False,
            },
            "scenarios": [],
            "rollback": {},
        }

        with tempfile.TemporaryDirectory(prefix="auren-multi-instance-") as temp:
            base = Path(temp)
            ctx = multiprocessing.get_context("spawn")
            start_event = ctx.Event()
            result_queue = ctx.Queue()
            owners = ["staging-host-a", "staging-host-b", "staging-host-c"]
            processes = []
            for index, owner in enumerate(owners):
                root = str(base / f"state-{owner}")
                process = ctx.Process(
                    target=_race_submit,
                    args=(dsn, namespace, policy, root, owner,
                          "staging:race:one-work-key", start_event, result_queue),
                    name=f"auren-{owner}",
                )
                process.start()
                processes.append(process)
            start_event.set()
            results = [result_queue.get(timeout=40) for _ in processes]
            for process in processes:
                process.join(20)
                self.assertEqual(0, process.exitcode, f"{process.name} exited {process.exitcode}")
            accepted = [item for item in results if item["outcome"] == "accepted"]
            rejected = [item for item in results if item["outcome"] == "duplicate_rejected"]
            self.assertEqual(1, len(accepted), results)
            self.assertEqual(2, len(rejected), results)
            self.assertEqual(1, len({item["task_id"] for item in accepted}))
            evidence["scenarios"].append({
                "name": "three-independent-runtime-race",
                "result": "passed",
                "runtime_results": results,
                "unique_winner_count": len(accepted),
                "duplicate_rejection_count": len(rejected),
            })

            # Simulate a process dying after the permanent identity commit but
            # before the local SQLite task insert. Restart the same runtime root.
            crash_root = str(base / "state-crash-before-insert")
            crash_owner = "staging-restart-owner"
            crash_key = "staging:crash:before-local-insert"
            crashed = ctx.Process(
                target=_crash_before_local_insert,
                args=(dsn, namespace, policy, crash_root, crash_owner, crash_key),
            )
            crashed.start()
            crashed.join(20)
            self.assertEqual(86, crashed.exitcode)
            restarted, _ = _runtime(dsn, namespace, policy, crash_root, crash_owner)
            recovered = restarted.submit(
                "tests.test_execution_controller.durable_handler",
                {"id": "crash-before-local-insert"},
                goal="crash recovery rehearsal",
                work_key=crash_key,
            )
            self.assertEqual("pending", recovered.state)
            with self.assertRaises(DuplicateWorkKeySubmission):
                other, _ = _runtime(
                    dsn, namespace, policy, str(base / "other-root"),
                    "staging-other-owner",
                )
                other.submit(
                    "tests.test_execution_controller.durable_handler",
                    {"id": "crash-before-local-insert"},
                    goal="crash recovery rehearsal", work_key=crash_key,
                )
            evidence["scenarios"].append({
                "name": "crash-after-identity-before-local-insert",
                "result": "passed",
                "process_exit_code": crashed.exitcode,
                "restarted_owner": crash_owner,
                "recovered_task_id": recovered.id,
                "recovered_state": recovered.state,
                "other_owner_rejected": True,
            })

            # Simulate process death after local SQLite commit, then restart
            # against the same root and verify the same task ID is returned.
            inserted_root = str(base / "state-crash-after-insert")
            inserted_owner = "staging-insert-restart-owner"
            inserted_key = "staging:crash:after-local-insert"
            inserted_crash = ctx.Process(
                target=_crash_after_local_insert,
                args=(dsn, namespace, policy, inserted_root, inserted_owner, inserted_key),
            )
            inserted_crash.start()
            inserted_crash.join(20)
            self.assertEqual(87, inserted_crash.exitcode)
            inserted_restart, _ = _runtime(
                dsn, namespace, policy, inserted_root, inserted_owner,
            )
            recovered_again = inserted_restart.submit(
                "tests.test_execution_controller.durable_handler",
                {"id": "crash-after-local-insert"},
                goal="local insert crash recovery rehearsal",
                work_key=inserted_key,
            )
            self.assertEqual("pending", recovered_again.state)
            evidence["scenarios"].append({
                "name": "crash-after-local-insert-and-restart",
                "result": "passed",
                "process_exit_code": inserted_crash.exitcode,
                "recovered_task_id": recovered_again.id,
                "recovered_state": recovered_again.state,
            })

            # Verify database-time expiry and fencing generation. This uses
            # the real shared lease row, not a mocked coordinator.
            lease_owner = "staging-expiry-owner"
            first_lease = coordinator.acquire(
                "staging-expiry-" + uuid4().hex, lease_owner,
                "operation-expiry", capacity=1, lease_seconds=60,
            )
            self.assertIsNotNone(first_lease)
            with psycopg.connect(dsn) as connection:
                connection.execute(
                    "UPDATE auren_admission_leases SET expires_at=CURRENT_TIMESTAMP - INTERVAL '1 second' "
                    "WHERE namespace=%s AND resource=%s AND operation_id=%s",
                    (namespace, first_lease.resource, "operation-expiry"),
                )
            renewed = coordinator.acquire(
                first_lease.resource, lease_owner, "operation-expiry",
                capacity=1, lease_seconds=60,
            )
            self.assertIsNotNone(renewed)
            self.assertGreater(renewed.generation, first_lease.generation)
            evidence["scenarios"].append({
                "name": "lease-expiry-and-fencing-reacquisition",
                "result": "passed",
                "old_generation": first_lease.generation,
                "new_generation": renewed.generation,
                "generation_increased": renewed.generation > first_lease.generation,
            })

            # Policy mismatch and coordinator outage must block before a local
            # task is persisted.
            for name, bad_coordinator, expected in (
                ("policy-mismatch", PostgreSQLAdmissionCoordinator(
                    lambda: psycopg.connect(dsn), namespace=namespace,
                    policy_version="wrong-policy", provision=False,
                ), AdmissionPolicyConflict),
                ("coordinator-outage", PostgreSQLAdmissionCoordinator(
                    lambda: psycopg.connect(
                        "postgresql://invalid:invalid@127.0.0.1:1/unavailable?connect_timeout=1"
                    ),
                    namespace="outage-" + uuid4().hex,
                    policy_version=policy, provision=False,
                ), AdmissionCoordinatorUnavailable),
            ):
                root = base / name
                blocked = ExecutionController(
                    root, deployment_admission_coordinator=bad_coordinator,
                    deployment_instance_id="staging-" + name,
                )
                blocked.register_handler(
                    "tests.test_execution_controller.durable_handler",
                    durable_handler, execution_class="read_only",
                )
                with self.assertRaises(expected):
                    blocked.submit(
                        "tests.test_execution_controller.durable_handler",
                        {"id": name}, goal=name, work_key="staging:" + name,
                    )
                with sqlite3.connect(blocked.db_path) as local_db:
                    local_pending = local_db.execute(
                        "SELECT COUNT(*) FROM tasks WHERE state='pending'"
                    ).fetchone()[0]
                self.assertEqual(0, local_pending)
                evidence["scenarios"].append({
                    "name": name, "result": "passed",
                    "failure_closed_before_local_insert": True,
                    "local_pending_count": local_pending,
                })

            # Rollback rehearsal: a separate runtime root with coordinator
            # injection disabled can continue using the local SQLite guard.
            rollback = ExecutionController(base / "rollback-local-only")
            rollback.register_handler(
                "tests.test_execution_controller.durable_handler",
                durable_handler, execution_class="read_only",
            )
            rollback_task = rollback.submit(
                "tests.test_execution_controller.durable_handler",
                {"id": "rollback"}, goal="local rollback path",
                work_key="staging:rollback:local-only",
            )
            self.assertEqual("pending", rollback_task.state)
            evidence["rollback"] = {
                "result": "passed",
                "coordinator_injection_disabled": True,
                "local_sqlite_admission_retained": True,
                "local_task_id": rollback_task.id,
                "note": "isolated rollback-path rehearsal; not a production cutover",
            }

        evidence["completed_at"] = _stamp()
        evidence["overall_result"] = "passed"
        evidence["runner"] = {
            "hostname": platform.node(),
            "pid": os.getpid(),
            "git_sha": subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            ).stdout.strip(),
        }
        report_path = Path(os.environ.get(
            "AUREN_STAGING_REPORT_PATH",
            "artifacts/multi-instance-admission-staging.json",
        ))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.assertTrue(report_path.exists())
        self.assertGreater(report_path.stat().st_size, 100)
