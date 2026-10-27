from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
import time
import uuid
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from portable.graph_workspace_adoption import GraphWorkspaceCoordinator
from portable.engineering_recovery import EngineeringRecoveryLedger, GraphRunRecoveryBridge
from portable.agency_evolution_gates import ImprovementJournal
from portable.graph_worker_lifecycle import GraphWorkerLifecycle, GraphWorkerLifecycleError
from portable.independent_graph_verification import IndependentGraphVerifier
from portable.progress_journal import ProgressJournal
from portable.runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard
from portable.task_planner import Task, TaskPlan


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True,
        check=False, shell=False, timeout=20,
    )
    if result.returncode:
        raise AssertionError(f"git {args!r} failed: {result.stderr}")
    return result.stdout.strip()


def init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "config", "user.name", "AUREN Test")
    git(root, "config", "user.email", "auren-test@example.invalid")
    (root / "README.md").write_text("# seed\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", "README.md", "src/base.py")
    git(root, "commit", "-qm", "initial")


class GraphWorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        init_repo(self.repo)
        self.intent_digest = hashlib.sha256(b"intent").hexdigest()
        self.journal = ProgressJournal(self.root / "progress.jsonl")
        self.guard = RuntimeExecutionGuard(max_steps=20, max_attempts=20, deadline_seconds=60)
        self.coordinator = GraphWorkspaceCoordinator(
            self.repo, workspace_root=self.root / "worker-workspaces"
        )
        self.verifier = IndependentGraphVerifier(
            self.repo, verifier_identity="verifier-ci",
            trusted_verifier_ids=("verifier-ci",),
            worktree_root=self.root / "verification-workspaces",
        )

    def tearDown(self):
        self.temp.cleanup()

    def lifecycle(self, tasks):
        task_plan = TaskPlan(tasks)
        run_id = "graph-lifecycle-" + uuid.uuid4().hex
        recovery_ledger = EngineeringRecoveryLedger(self.root / (run_id + "-recovery.sqlite"))
        recovery_journal = ImprovementJournal(self.root / (run_id + "-recovery.jsonl"))
        recovery_bridge = GraphRunRecoveryBridge(
            run_id=run_id, plan=task_plan, intent_digest=self.intent_digest,
            source_revision=self.coordinator.base_revision,
            ledger=recovery_ledger, journal=recovery_journal,
        )
        return GraphWorkerLifecycle(
            coordinator=self.coordinator, verifier=self.verifier,
            journal=self.journal, task_plan=task_plan,
            intent_digest=self.intent_digest, runtime_guard=self.guard,
            recovery_bridge=recovery_bridge,
        )

    @staticmethod
    def checks(path: str, expected: str):
        code = (
            "from pathlib import Path; "
            f"assert Path({path!r}).read_text(encoding='utf-8') == {expected!r}"
        )
        return ({"check_id": "content-check", "argv": ["python", "-c", code], "timeout_seconds": 20},)

    def test_mutating_node_uses_fresh_verification_and_serialized_adoption(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])
        def write(workspace, _task):
            (workspace / "src" / "feature.py").write_text("verified = True\n", encoding="utf-8")
        result = plan.run(
            task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
            owned_paths=["src/feature.py"], execute_in_workspace=write,
            checks=self.checks("src/feature.py", "verified = True\n"),
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["workers"]["worker-a"]["external_evaluator_independence"], "not_proven")
        self.assertEqual((self.repo / "src" / "feature.py").read_text(encoding="utf-8"), "verified = True\n")
        self.assertEqual(self.journal.snapshot("write-a")["state"], "completed")
        self.assertEqual(plan.recovery_bridge.load()["nodes"]["write-a"]["status"], "complete")
        self.assertEqual(plan.recovery_bridge.load()["run_status"], "complete")
        self.assertTrue(Path(result["workers"]["worker-a"]["receipt_path"]).is_file())

    def test_failed_independent_check_never_adopts_or_reports_success(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])
        def write(workspace, _task):
            (workspace / "src" / "feature.py").write_text("not-verified = True\n", encoding="utf-8")
        with self.assertRaisesRegex(Exception, "verification receipt binding mismatch"):
            plan.run(
                task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
                owned_paths=["src/feature.py"], execute_in_workspace=write,
                checks=self.checks("src/feature.py", "expected = True\n"),
            )
        self.assertFalse((self.repo / "src" / "feature.py").exists())
        self.assertEqual(self.journal.snapshot("write-a")["state"], "failed")
        self.assertEqual(plan.recovery_bridge.load()["nodes"]["write-a"]["status"], "failed")
        self.assertEqual(plan.recovery_bridge.load()["run_status"], "blocked")

    def test_contradictory_check_evidence_cannot_be_laundered_by_passed_receipt(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])

        def write(workspace, _task):
            (workspace / "src" / "feature.py").write_text("verified = True\n", encoding="utf-8")

        original_verify = plan.verifier.verify

        def contradictory_receipt(*args, **kwargs):
            receipt = original_verify(*args, **kwargs)
            receipt["checks"][0]["status"] = "failed"
            unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
            receipt["receipt_digest"] = hashlib.sha256(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            ).hexdigest()
            return receipt

        with patch.object(plan.verifier, "verify", side_effect=contradictory_receipt):
            with self.assertRaisesRegex(GraphWorkerLifecycleError, "contradictory check evidence"):
                plan.prepare(
                    task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
                    owned_paths=["src/feature.py"], execute_in_workspace=write,
                    checks=self.checks("src/feature.py", "verified = True\\n"),
                )

        self.assertFalse((self.repo / "src" / "feature.py").exists())
        self.assertEqual(self.journal.snapshot("write-a")["state"], "failed")
        self.assertEqual(plan.recovery_bridge.load()["nodes"]["write-a"]["status"], "failed")

    def test_missing_check_evidence_cannot_be_laundered_by_passed_receipt(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])

        def write(workspace, _task):
            (workspace / "src" / "feature.py").write_text("verified = True\n", encoding="utf-8")

        original_verify = plan.verifier.verify

        def incomplete_receipt(*args, **kwargs):
            receipt = original_verify(*args, **kwargs)
            receipt["checks"] = receipt["checks"][:1]
            unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
            receipt["receipt_digest"] = hashlib.sha256(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            ).hexdigest()
            return receipt

        checks = list(self.checks("src/feature.py", "verified = True\n"))
        checks.append({**checks[0], "check_id": "content-check-secondary"})
        with patch.object(plan.verifier, "verify", side_effect=incomplete_receipt):
            with self.assertRaisesRegex(GraphWorkerLifecycleError, "missing required check evidence"):
                plan.prepare(
                    task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
                    owned_paths=["src/feature.py"], execute_in_workspace=write,
                    checks=checks,
                )

        self.assertFalse((self.repo / "src" / "feature.py").exists())
        self.assertEqual(self.journal.snapshot("write-a")["state"], "failed")
        self.assertEqual(plan.recovery_bridge.load()["nodes"]["write-a"]["status"], "failed")

    def test_lifecycle_rejects_recovery_checkpoint_bound_to_different_intent(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])
        changed_intent = hashlib.sha256(b"changed intent").hexdigest()
        with self.assertRaisesRegex(ValueError, "intent or source revision"):
            GraphWorkerLifecycle(
                coordinator=plan.coordinator,
                verifier=plan.verifier,
                journal=plan.journal,
                task_plan=plan.task_plan,
                intent_digest=changed_intent,
                runtime_guard=plan.runtime_guard,
                recovery_bridge=plan.recovery_bridge,
            )
        self.assertEqual(plan.recovery_bridge.load()["run_status"], "in_progress")
        self.assertFalse((self.repo / "src" / "feature.py").exists())

    def test_cancelled_running_node_stays_unknown_until_termination_is_confirmed(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])
        self.guard.charge_step()
        self.guard.charge_attempt("graph-worker:write-a:worker-a")
        plan.recovery_bridge.record_node(
            event_id="cancel-worker-started",
            node_id="write-a",
            status="working",
            budget_snapshot=self.guard.snapshot(),
            next_safe_action="execute_worker_and_capture_proposal",
            worker_id="worker-a",
            workspace_id="workspace-a",
            attempts=1,
        )
        self.guard.cancel("cancellation requested; worker termination not confirmed")
        snapshot = plan.recovery_bridge.cancel_run(
            event_id="cancel-worker-run",
            reason="cancellation requested; worker termination not confirmed",
            next_safe_action="reconcile_worker_termination_before_resume",
            budget_snapshot=self.guard.snapshot(),
        )
        self.assertEqual(snapshot["run_status"], "cancelled")
        self.assertEqual(snapshot["nodes"]["write-a"]["status"], "unknown")
        self.assertEqual(
            snapshot["nodes"]["write-a"]["next_safe_action"],
            "reconcile_worker_termination_before_resume",
        )
        self.assertEqual(plan.recovery_bridge.dispatchable_nodes(), ())

    def test_process_restart_after_cancellation_keeps_unconfirmed_worker_unknown(self):
        state_root = self.root / "cancel-crash-state"
        state_root.mkdir()
        source_revision = git(self.repo, "rev-parse", "HEAD")
        script = textwrap.dedent("""\
            import hashlib
            import os
            import sys
            from pathlib import Path
            from portable.agency_evolution_gates import ImprovementJournal
            from portable.engineering_recovery import EngineeringRecoveryLedger, GraphRunRecoveryBridge
            from portable.graph_worker_lifecycle import GraphWorkerLifecycle
            from portable.graph_workspace_adoption import GraphWorkspaceCoordinator
            from portable.independent_graph_verification import IndependentGraphVerifier
            from portable.progress_journal import ProgressJournal
            from portable.runtime_recovery import RuntimeExecutionGuard
            from portable.task_planner import Task, TaskPlan

            repo = Path(sys.argv[1])
            state_root = Path(sys.argv[2])
            source_revision = sys.argv[3]
            run_id = "graph-cancel-crash-boundary"
            intent_digest = hashlib.sha256(b"cancel-intent").hexdigest()
            task_plan = TaskPlan([
                Task("write-a", "write one file", files=["src/feature.py"],
                     metadata={"execution_mode": "mutating"})
            ])
            progress = ProgressJournal(state_root / "progress.jsonl")
            guard = RuntimeExecutionGuard(max_steps=20, max_attempts=20, deadline_seconds=60)
            coordinator = GraphWorkspaceCoordinator(
                repo, workspace_root=state_root / "worker-workspaces"
            )
            verifier = IndependentGraphVerifier(
                repo, verifier_identity="verifier-ci", trusted_verifier_ids=("verifier-ci",),
                worktree_root=state_root / "verification-workspaces",
            )
            ledger = EngineeringRecoveryLedger(state_root / "recovery.sqlite")
            recovery_journal = ImprovementJournal(state_root / "recovery.jsonl")
            bridge = GraphRunRecoveryBridge(
                run_id=run_id, plan=task_plan, intent_digest=intent_digest,
                source_revision=source_revision, ledger=ledger, journal=recovery_journal,
            )
            GraphWorkerLifecycle(
                coordinator=coordinator, verifier=verifier, journal=progress,
                task_plan=task_plan, intent_digest=intent_digest,
                runtime_guard=guard, recovery_bridge=bridge,
            )
            guard.charge_step()
            guard.charge_attempt("graph-worker:write-a:worker-a")
            progress.record(
                "write-a", "worker-started", state="running", stage="isolated_workspace",
                message="worker lease started before cancellation",
            )
            bridge.record_node(
                event_id="cancel-worker-started", node_id="write-a", status="working",
                budget_snapshot=guard.snapshot(),
                next_safe_action="execute_worker_and_capture_proposal",
                worker_id="worker-a", workspace_id="workspace-a", attempts=1,
            )
            guard.cancel("cancel requested; child termination not confirmed")
            bridge.cancel_run(
                event_id="cancel-worker-run",
                reason="cancel requested; child termination not confirmed",
                next_safe_action="reconcile_worker_termination_before_resume",
                budget_snapshot=guard.snapshot(),
            )
            os._exit(87)
        """)
        child = subprocess.run(
            [sys.executable, "-c", script, str(self.repo), str(state_root), source_revision],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=False, timeout=60,
        )
        self.assertEqual(child.returncode, 87, child.stdout + child.stderr)

        run_id = "graph-cancel-crash-boundary"
        task_plan = TaskPlan([
            Task("write-a", "write one file", files=["src/feature.py"],
                 metadata={"execution_mode": "mutating"})
        ])
        bridge = GraphRunRecoveryBridge(
            run_id=run_id, plan=task_plan, intent_digest=hashlib.sha256(b"cancel-intent").hexdigest(),
            source_revision=source_revision,
            ledger=EngineeringRecoveryLedger(state_root / "recovery.sqlite"),
            journal=ImprovementJournal(state_root / "recovery.jsonl"),
        )
        snapshot = bridge.load()
        self.assertEqual(snapshot["run_status"], "cancelled")
        self.assertEqual(snapshot["nodes"]["write-a"]["status"], "unknown")
        self.assertEqual(
            snapshot["nodes"]["write-a"]["next_safe_action"],
            "reconcile_worker_termination_before_resume",
        )
        self.assertEqual(bridge.dispatchable_nodes(), ())

        coordinator = GraphWorkspaceCoordinator(
            self.repo, workspace_root=state_root / "restart-worker-workspaces"
        )
        verifier = IndependentGraphVerifier(
            self.repo, verifier_identity="verifier-ci", trusted_verifier_ids=("verifier-ci",),
            worktree_root=state_root / "restart-verification-workspaces",
        )
        restarted = GraphWorkerLifecycle(
            coordinator=coordinator, verifier=verifier,
            journal=ProgressJournal(state_root / "progress.jsonl"),
            task_plan=task_plan,
            intent_digest=hashlib.sha256(b"cancel-intent").hexdigest(),
            runtime_guard=RuntimeExecutionGuard.resume(snapshot["budget_snapshot"]),
            recovery_bridge=bridge,
        )
        dispatches = []
        with self.assertRaisesRegex(PermissionError, "durable progress already exists"):
            restarted.prepare(
                task_id="write-a", worker_id="worker-a-retry", worker_identity="worker-a-retry",
                owned_paths=["src/feature.py"],
                execute_in_workspace=lambda *_args: dispatches.append(True),
                checks=(),
            )
        self.assertEqual(dispatches, [])
        recovered = bridge.load()
        self.assertEqual(recovered["run_status"], "cancelled")
        self.assertEqual(recovered["nodes"]["write-a"]["status"], "unknown")
        self.assertEqual(bridge.dispatchable_nodes(), ())
        self.assertFalse((self.repo / "src" / "feature.py").exists())

    def test_stale_plan_digest_blocks_adoption_after_verification(self):
        tasks = [Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})]
        plan = self.lifecycle(tasks)
        def write(workspace, _task):
            (workspace / "src" / "feature.py").write_text("verified = True\n", encoding="utf-8")
        prepared = plan.prepare(
            task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
            owned_paths=["src/feature.py"], execute_in_workspace=write,
            checks=self.checks("src/feature.py", "verified = True\n"),
        )
        tasks[0].metadata["intent_scope"] = "changed-after-verification"
        with self.assertRaisesRegex(PermissionError, "TaskPlan or intent changed"):
            plan.adopt_prepared((prepared.worker_id,))
        self.assertFalse((self.repo / "src" / "feature.py").exists())

    def test_disjoint_verified_workers_share_one_adoption_node(self):
        tasks = [
            Task("write-a", "write file a", files=["src/a.py"], metadata={"execution_mode": "mutating"}),
            Task("write-b", "write file b", files=["src/b.py"], metadata={"execution_mode": "mutating"}),
        ]
        plan = self.lifecycle(tasks)
        prepared = {}
        def writer(path, value):
            def write(workspace, _task):
                (workspace / path).write_text(value, encoding="utf-8")
            return write
        first = plan.prepare(
            task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
            owned_paths=["src/a.py"], execute_in_workspace=writer("src/a.py", "a = 1\n"),
            checks=self.checks("src/a.py", "a = 1\n"),
        )
        second = plan.prepare(
            task_id="write-b", worker_id="worker-b", worker_identity="worker-b",
            owned_paths=["src/b.py"], execute_in_workspace=writer("src/b.py", "b = 2\n"),
            checks=self.checks("src/b.py", "b = 2\n"),
        )
        prepared[first.worker_id] = first
        prepared[second.worker_id] = second
        result = plan.adopt_prepared(("worker-a", "worker-b"))
        self.assertEqual(result["status"], "completed")
        self.assertTrue((self.repo / "src" / "a.py").is_file())
        self.assertTrue((self.repo / "src" / "b.py").is_file())
        self.assertEqual(self.journal.snapshot("write-a")["state"], "completed")
        self.assertEqual(self.journal.snapshot("write-b")["state"], "completed")
        self.assertEqual(plan.recovery_bridge.load()["run_status"], "complete")

    def test_state_graph_runs_worker_nodes_then_one_serialized_adoption_node(self):
        from portable.agency_state_graph import StateGraph

        tasks = [
            Task("write-a", "write file a", files=["src/a.py"], metadata={"execution_mode": "mutating"}),
            Task("write-b", "write file b", files=["src/b.py"], metadata={"execution_mode": "mutating"}),
        ]
        plan = self.lifecycle(tasks)
        prepared = {}
        def writer(path, value):
            def write(workspace, _task):
                (workspace / path).write_text(value, encoding="utf-8")
            return write
        graph = StateGraph()
        graph.add_node("worker-a", plan.as_prepare_node(
            prepared_workers=prepared, task_id="write-a", worker_id="worker-a",
            worker_identity="worker-a", owned_paths=["src/a.py"],
            execute_in_workspace=writer("src/a.py", "a = 1\\n"),
            checks=self.checks("src/a.py", "a = 1\\n"),
        ))
        graph.add_node("worker-b", plan.as_prepare_node(
            prepared_workers=prepared, task_id="write-b", worker_id="worker-b",
            worker_identity="worker-b", owned_paths=["src/b.py"],
            execute_in_workspace=writer("src/b.py", "b = 2\\n"),
            checks=self.checks("src/b.py", "b = 2\\n"),
        ))
        graph.add_node("adopt", plan.as_adoption_node(
            prepared_workers=prepared, worker_ids=("worker-a", "worker-b"),
        ))
        graph.add_edge(StateGraph.START, "worker-a")
        graph.add_edge(StateGraph.START, "worker-b")
        graph.add_edge("worker-a", "adopt")
        graph.add_edge("worker-b", "adopt")
        graph.add_edge("adopt", StateGraph.END)
        result = graph.compile().invoke(
            {}, run_id="graph-worker-lifecycle",
            parallel_nodes=lambda name: name.startswith("worker-"),
            max_parallel_nodes=2,
            node_duration_estimates={"worker-a": 5.0, "worker-b": 5.0},
            parallel_overhead_seconds=0.001,
        )
        self.assertFalse(result.interrupted)
        self.assertEqual(result.state["graph_worker_adoption"]["status"], "completed")
        self.assertTrue((self.repo / "src" / "a.py").is_file())
        self.assertTrue((self.repo / "src" / "b.py").is_file())

    def test_deadline_overrun_fails_worker_before_adoption(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])
        def slow_write(workspace, _task):
            (workspace / "src" / "feature.py").write_text("late = True\\n", encoding="utf-8")
            self.guard.tighten_deadline(0.01)
            time.sleep(0.03)
        with self.assertRaises(ExecutionBudgetExceeded):
            plan.run(
                task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
                owned_paths=["src/feature.py"], execute_in_workspace=slow_write,
                checks=self.checks("src/feature.py", "late = True\\n"),
            )
        self.assertFalse((self.repo / "src" / "feature.py").exists())
        self.assertEqual(self.journal.snapshot("write-a")["state"], "failed")

    def test_lost_prepared_worker_state_is_blocked_on_restart(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])
        def write(workspace, _task):
            (workspace / "src" / "feature.py").write_text("verified = True\\n", encoding="utf-8")
        prepared = plan.prepare(
            task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
            owned_paths=["src/feature.py"], execute_in_workspace=write,
            checks=self.checks("src/feature.py", "verified = True\\n"),
        )
        node = plan.as_adoption_node(prepared_workers={}, worker_ids=("worker-a",))
        state = {"graph_worker_prepared_worker-a": {
            "worker_id": "worker-a", "task_id": "write-a", "status": "verified",
            "receipt_path": prepared.receipt_path,
        }}
        with self.assertRaisesRegex(PermissionError, "restart reconciliation"):
            node(state)
        self.assertEqual(self.journal.snapshot("write-a")["state"], "blocked")
        self.assertEqual(plan.recovery_bridge.load()["nodes"]["write-a"]["status"], "unknown")
        self.assertEqual(plan.recovery_bridge.load()["run_status"], "blocked")
        self.assertFalse((self.repo / "src" / "feature.py").exists())

    def test_shared_attempt_budget_blocks_second_worker_without_reset(self):
        tasks = [
            Task("write-a", "write file a", files=["src/a.py"], metadata={"execution_mode": "mutating"}),
            Task("write-b", "write file b", files=["src/b.py"], metadata={"execution_mode": "mutating"}),
        ]
        self.guard = RuntimeExecutionGuard(max_steps=10, max_attempts=1, deadline_seconds=60)
        plan = self.lifecycle(tasks)
        def write_a(workspace, _task):
            (workspace / "src" / "a.py").write_text("a = 1\\n", encoding="utf-8")
        plan.prepare(
            task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
            owned_paths=["src/a.py"], execute_in_workspace=write_a,
            checks=self.checks("src/a.py", "a = 1\\n"),
        )
        with self.assertRaises(ExecutionBudgetExceeded):
            plan.prepare(
                task_id="write-b", worker_id="worker-b", worker_identity="worker-b",
                owned_paths=["src/b.py"], execute_in_workspace=lambda *_: None,
                checks=self.checks("src/b.py", "b = 2\\n"),
            )
        self.assertEqual(self.guard.snapshot()["attempts_used"], 1)
        self.assertFalse((self.repo / "src" / "b.py").exists())

    def test_tampered_persisted_receipt_blocks_adoption_and_records_failure(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])
        def write(workspace, _task):
            (workspace / "src" / "feature.py").write_text("verified = True\\n", encoding="utf-8")
        prepared = plan.prepare(
            task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
            owned_paths=["src/feature.py"], execute_in_workspace=write,
            checks=self.checks("src/feature.py", "verified = True\\n"),
        )
        Path(prepared.receipt_path).write_text("{}\\n", encoding="utf-8")
        with self.assertRaisesRegex(PermissionError, "persisted verification receipt"):
            plan.adopt_prepared((prepared.worker_id,))
        self.assertFalse((self.repo / "src" / "feature.py").exists())
        self.assertEqual(self.journal.snapshot("write-a")["state"], "failed")
        self.assertEqual(plan.recovery_bridge.load()["nodes"]["write-a"]["status"], "failed")

    def test_dependency_blocker_is_durable_and_requires_reconciliation(self):
        tasks = [
            Task("parent", "parent task"),
            Task("child", "child write", dependencies=["parent"], files=["src/child.py"],
                 metadata={"execution_mode": "mutating"}),
        ]
        plan = self.lifecycle(tasks)
        write = lambda workspace, _task: (workspace / "src" / "child.py").write_text("child = True\\n", encoding="utf-8")
        with self.assertRaisesRegex(PermissionError, "unresolved dependencies"):
            plan.prepare(
                task_id="child", worker_id="worker-child", worker_identity="worker-child",
                owned_paths=["src/child.py"], execute_in_workspace=write,
                checks=self.checks("src/child.py", "child = True\\n"),
            )
        blocked = self.journal.snapshot("child")
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn("blocked_by", blocked["message"])
        tasks[0].status = "done"
        plan.recovery_bridge.record_node(
            event_id="parent-start", node_id="parent", status="working",
            budget_snapshot=self.guard.snapshot(), next_safe_action="complete_parent",
            worker_id="worker-parent", workspace_id="workspace-parent", attempts=1,
        )
        plan.recovery_bridge.record_node(
            event_id="parent-complete", node_id="parent", status="complete",
            budget_snapshot=self.guard.snapshot(), next_safe_action="continue_dependency_ready_nodes",
            worker_id="worker-parent", workspace_id="workspace-parent", attempts=1,
            evidence_refs=("test://parent-completion",),
        )
        with self.assertRaisesRegex(PermissionError, "only a recorded dependency blocker"):
            plan.reconcile_dependencies("parent")
        plan.reconcile_dependencies("child")
        result = plan.run(
            task_id="child", worker_id="worker-child", worker_identity="worker-child",
            owned_paths=["src/child.py"], execute_in_workspace=write,
            checks=self.checks("src/child.py", "child = True\\n"),
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.journal.snapshot("child")["state"], "completed")
        self.assertEqual(plan.recovery_bridge.load()["nodes"]["child"]["status"], "complete")
        self.assertEqual(plan.recovery_bridge.load()["run_status"], "complete")

    def test_restart_or_duplicate_dispatch_fails_closed_from_durable_journal(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])
        self.journal.record("write-a", "worker-started-before-crash", state="running", stage="isolated_workspace")
        with self.assertRaisesRegex(PermissionError, "reconcile prior worker state"):
            plan.prepare(
                task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
                owned_paths=["src/feature.py"], execute_in_workspace=lambda *_: None,
                checks=self.checks("src/feature.py", "unused"),
            )
        self.assertEqual(self.journal.snapshot("write-a")["state"], "blocked")
        self.assertFalse((self.repo / "src" / "feature.py").exists())


    def test_cleanup_failure_after_apply_keeps_task_blocked_until_reconciliation(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])

        def write(workspace, _task):
            (workspace / "src" / "feature.py").write_text("verified = True\\n", encoding="utf-8")

        prepared = plan.prepare(
            task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
            owned_paths=["src/feature.py"], execute_in_workspace=write,
            checks=self.checks("src/feature.py", "verified = True\\n"),
        )
        with patch.object(self.coordinator, "cleanup_worker", side_effect=RuntimeError("injected cleanup failure")):
            with self.assertRaisesRegex(RuntimeError, "injected cleanup failure"):
                plan.adopt_prepared((prepared.worker_id,))

        self.assertTrue((self.repo / "src" / "feature.py").is_file())
        self.assertEqual(plan.task_plan.tasks["write-a"].status, "pending")
        self.assertEqual(self.journal.snapshot("write-a")["state"], "blocked")
        snapshot = plan.recovery_bridge.load()
        self.assertEqual(snapshot["nodes"]["write-a"]["status"], "unknown")
        self.assertEqual(snapshot["run_status"], "blocked")

    def test_recovery_commit_failure_corrects_terminal_journal_and_blocks_run(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])

        def write(workspace, _task):
            (workspace / "src" / "feature.py").write_text("verified = True\\n", encoding="utf-8")

        prepared = plan.prepare(
            task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
            owned_paths=["src/feature.py"], execute_in_workspace=write,
            checks=self.checks("src/feature.py", "verified = True\\n"),
        )
        record_recovery_node = plan._record_recovery_node

        def fail_completion_commit(**kwargs):
            if kwargs.get("status") == "complete":
                raise OSError("injected recovery commit failure")
            return record_recovery_node(**kwargs)

        with patch.object(plan, "_record_recovery_node", side_effect=fail_completion_commit):
            with self.assertRaisesRegex(OSError, "injected recovery commit failure"):
                plan.adopt_prepared((prepared.worker_id,))

        self.assertTrue((self.repo / "src" / "feature.py").is_file())
        self.assertEqual(plan.task_plan.tasks["write-a"].status, "pending")
        self.assertEqual(self.journal.snapshot("write-a")["state"], "blocked")
        snapshot = plan.recovery_bridge.load()
        self.assertEqual(snapshot["nodes"]["write-a"]["status"], "unknown")
        self.assertEqual(snapshot["run_status"], "blocked")


    def test_worker_failure_marks_cancelled_sibling_unknown_before_run_blocks(self):
        plan = self.lifecycle([
            Task("write-a", "write file a", files=["src/a.py"], metadata={"execution_mode": "mutating"}),
            Task("write-b", "write file b", files=["src/b.py"], metadata={"execution_mode": "mutating"}),
        ])

        def write_a(workspace, _task):
            (workspace / "src" / "a.py").write_text("a = True\\n", encoding="utf-8")

        plan.prepare(
            task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
            owned_paths=["src/a.py"], execute_in_workspace=write_a,
            checks=self.checks("src/a.py", "a = True\\n"),
        )

        def write_b(workspace, _task):
            (workspace / "src" / "b.py").write_text("b = False\\n", encoding="utf-8")

        with self.assertRaisesRegex(Exception, "verification receipt binding mismatch"):
            plan.prepare(
                task_id="write-b", worker_id="worker-b", worker_identity="worker-b",
                owned_paths=["src/b.py"], execute_in_workspace=write_b,
                checks=self.checks("src/b.py", "b = True\\n"),
            )

        self.assertFalse((self.repo / "src" / "a.py").exists())
        self.assertFalse((self.repo / "src" / "b.py").exists())
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "cancel_requested")
        self.assertEqual(self.journal.snapshot("write-a")["state"], "blocked")
        self.assertEqual(self.journal.snapshot("write-b")["state"], "failed")
        snapshot = plan.recovery_bridge.load()
        self.assertEqual(snapshot["nodes"]["write-a"]["status"], "unknown")
        self.assertEqual(snapshot["nodes"]["write-b"]["status"], "failed")
        self.assertEqual(snapshot["run_status"], "blocked")


    def test_process_crash_after_filesystem_apply_requires_restart_reconciliation(self):
        state_root = self.root / "crash-state"
        state_root.mkdir()
        script = textwrap.dedent("""\
            import hashlib
            import os
            import sys
            from pathlib import Path
            from portable.agency_evolution_gates import ImprovementJournal
            from portable.engineering_recovery import EngineeringRecoveryLedger, GraphRunRecoveryBridge
            from portable.graph_worker_lifecycle import GraphWorkerLifecycle
            from portable.graph_workspace_adoption import GraphWorkspaceCoordinator
            from portable.independent_graph_verification import IndependentGraphVerifier
            from portable.progress_journal import ProgressJournal
            from portable.runtime_recovery import RuntimeExecutionGuard
            from portable.task_planner import Task, TaskPlan

            repo = Path(sys.argv[1])
            state_root = Path(sys.argv[2])
            intent_digest = hashlib.sha256(b"intent").hexdigest()
            task_plan = TaskPlan([
                Task("write-a", "write one file", files=["src/feature.py"],
                     metadata={"execution_mode": "mutating"})
            ])
            journal = ProgressJournal(state_root / "progress.jsonl")
            guard = RuntimeExecutionGuard(max_steps=20, max_attempts=20, deadline_seconds=60)
            coordinator = GraphWorkspaceCoordinator(
                repo, workspace_root=state_root / "worker-workspaces"
            )
            verifier = IndependentGraphVerifier(
                repo, verifier_identity="verifier-ci", trusted_verifier_ids=("verifier-ci",),
                worktree_root=state_root / "verification-workspaces",
            )
            ledger = EngineeringRecoveryLedger(state_root / "recovery.sqlite")
            recovery_journal = ImprovementJournal(state_root / "recovery.jsonl")
            bridge = GraphRunRecoveryBridge(
                run_id="graph-crash-boundary", plan=task_plan,
                intent_digest=intent_digest, source_revision=coordinator.base_revision,
                ledger=ledger, journal=recovery_journal,
            )
            lifecycle = GraphWorkerLifecycle(
                coordinator=coordinator, verifier=verifier, journal=journal,
                task_plan=task_plan, intent_digest=intent_digest,
                runtime_guard=guard, recovery_bridge=bridge,
            )

            def write(workspace, _task):
                (workspace / "src" / "feature.py").write_text(
                    "verified = True\\n", encoding="utf-8"
                )

            check_code = (
                "from pathlib import Path; "
                "assert Path('src/feature.py').read_text(encoding='utf-8') == "
                + repr("verified = True\\n")
            )
            prepared = lifecycle.prepare(
                task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
                owned_paths=["src/feature.py"], execute_in_workspace=write,
                checks=({
                    "check_id": "content-check",
                    "argv": [sys.executable, "-c", check_code],
                    "timeout_seconds": 20,
                },),
            )
            original_adopt_many = coordinator.adopt_many

            def crash_after_apply(proposals):
                original_adopt_many(proposals)
                os._exit(86)

            coordinator.adopt_many = crash_after_apply
            lifecycle.adopt_prepared((prepared.worker_id,))
            os._exit(87)
        """)
        child = subprocess.run(
            [sys.executable, "-c", script, str(self.repo), str(state_root)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=False, timeout=90,
        )
        self.assertEqual(child.returncode, 86, child.stdout + child.stderr)
        self.assertEqual(
            (self.repo / "src" / "feature.py").read_text(encoding="utf-8"),
            "verified = True\n",
        )

        progress = ProgressJournal(state_root / "progress.jsonl").snapshot("write-a")
        self.assertEqual(progress["state"], "running")
        self.assertEqual(progress["stage"], "adoption")
        self.assertIn("reconcile_target_tree_and_recovery_ledger", progress["message"])
        self.assertNotEqual(progress["state"], "completed")

        task_plan = TaskPlan([
            Task("write-a", "write one file", files=["src/feature.py"],
                 metadata={"execution_mode": "mutating"})
        ])
        ledger = EngineeringRecoveryLedger(state_root / "recovery.sqlite")
        recovery_journal = ImprovementJournal(state_root / "recovery.jsonl")
        bridge = GraphRunRecoveryBridge(
            run_id="graph-crash-boundary", plan=task_plan,
            intent_digest=self.intent_digest, source_revision=git(self.repo, "rev-parse", "HEAD"),
            ledger=ledger, journal=recovery_journal,
        )
        snapshot = bridge.load()
        self.assertEqual(snapshot["run_status"], "in_progress")
        self.assertEqual(snapshot["nodes"]["write-a"]["status"], "adopting")
        evidence_refs = snapshot["nodes"]["write-a"]["evidence_refs"]
        self.assertEqual(len(evidence_refs), 2)
        self.assertTrue(any(ref.endswith(".patch") for ref in evidence_refs))
        self.assertTrue(any(ref.endswith(".verification.json") for ref in evidence_refs))
        self.assertEqual(
            snapshot["nodes"]["write-a"]["next_safe_action"],
            "reconcile_target_tree_and_recovery_ledger",
        )
        self.assertEqual(bridge.dispatchable_nodes(), ())

        restarted = GraphWorkerLifecycle(
            coordinator=self.coordinator,
            verifier=self.verifier,
            journal=ProgressJournal(state_root / "progress.jsonl"),
            task_plan=task_plan,
            intent_digest=self.intent_digest,
            runtime_guard=RuntimeExecutionGuard.resume(snapshot["budget_snapshot"]),
            recovery_bridge=bridge,
        )
        with self.assertRaisesRegex(PermissionError, "durable progress already exists"):
            restarted.prepare(
                task_id="write-a",
                worker_id="worker-a",
                worker_identity="worker-a",
                owned_paths=["src/feature.py"],
                execute_in_workspace=lambda *_args: None,
                checks=(),
            )
        recovered = bridge.load()
        self.assertEqual(recovered["run_status"], "blocked")
        self.assertEqual(
            recovered["next_safe_action"],
            "reconcile_target_tree_and_recovery_ledger",
        )

        with self.assertRaisesRegex(PermissionError, "source checkout must be clean"):
            GraphWorkspaceCoordinator(
                self.repo, workspace_root=state_root / "restart-workspaces"
            )


    def test_crash_between_recovery_checkpoint_and_progress_event_blocks_redispatch(self):
        state_root = self.root / "journal-crash-state"
        state_root.mkdir()
        script = textwrap.dedent("""\
            import hashlib
            import os
            import sys
            from pathlib import Path
            from portable.agency_evolution_gates import ImprovementJournal
            from portable.engineering_recovery import EngineeringRecoveryLedger, GraphRunRecoveryBridge
            from portable.graph_worker_lifecycle import GraphWorkerLifecycle
            from portable.graph_workspace_adoption import GraphWorkspaceCoordinator
            from portable.independent_graph_verification import IndependentGraphVerifier
            from portable.progress_journal import ProgressJournal
            from portable.runtime_recovery import RuntimeExecutionGuard
            from portable.task_planner import Task, TaskPlan

            repo = Path(sys.argv[1])
            state_root = Path(sys.argv[2])
            intent_digest = hashlib.sha256(b"intent").hexdigest()
            task_plan = TaskPlan([
                Task("write-a", "write one file", files=["src/feature.py"],
                     metadata={"execution_mode": "mutating"})
            ])
            journal = ProgressJournal(state_root / "progress.jsonl")
            guard = RuntimeExecutionGuard(max_steps=20, max_attempts=20, deadline_seconds=60)
            coordinator = GraphWorkspaceCoordinator(
                repo, workspace_root=state_root / "worker-workspaces"
            )
            verifier = IndependentGraphVerifier(
                repo, verifier_identity="verifier-ci", trusted_verifier_ids=("verifier-ci",),
                worktree_root=state_root / "verification-workspaces",
            )
            ledger = EngineeringRecoveryLedger(state_root / "recovery.sqlite")
            recovery_journal = ImprovementJournal(state_root / "recovery.jsonl")
            bridge = GraphRunRecoveryBridge(
                run_id="graph-journal-crash-boundary", plan=task_plan,
                intent_digest=intent_digest, source_revision=coordinator.base_revision,
                ledger=ledger, journal=recovery_journal,
            )
            lifecycle = GraphWorkerLifecycle(
                coordinator=coordinator, verifier=verifier, journal=journal,
                task_plan=task_plan, intent_digest=intent_digest,
                runtime_guard=guard, recovery_bridge=bridge,
            )

            def write(workspace, _task):
                (workspace / "src" / "feature.py").write_text(
                    "verified = True\\n", encoding="utf-8"
                )

            check_code = (
                "from pathlib import Path; "
                "assert Path('src/feature.py').read_text(encoding='utf-8') == "
                + repr("verified = True\\n")
            )
            prepared = lifecycle.prepare(
                task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
                owned_paths=["src/feature.py"], execute_in_workspace=write,
                checks=({
                    "check_id": "content-check",
                    "argv": [sys.executable, "-c", check_code],
                    "timeout_seconds": 20,
                },),
            )
            original_record = journal.record

            def crash_before_progress_record(task_id, event, **kwargs):
                if event == "adoption-started":
                    # The recovery checkpoint is durable, but the progress event
                    # has not yet been written. Simulate immediate process death.
                    os._exit(85)
                return original_record(task_id, event, **kwargs)

            journal.record = crash_before_progress_record
            lifecycle.adopt_prepared((prepared.worker_id,))
            os._exit(86)
        """)
        child = subprocess.run(
            [sys.executable, "-c", script, str(self.repo), str(state_root)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=False, timeout=90,
        )
        self.assertEqual(child.returncode, 85, child.stdout + child.stderr)
        self.assertFalse((self.repo / "src" / "feature.py").exists())

        progress_journal = ProgressJournal(state_root / "progress.jsonl")
        progress = progress_journal.snapshot("write-a")
        self.assertEqual(progress["state"], "running")
        self.assertEqual(progress["stage"], "adoption")
        self.assertIn("await_serialized_adoption", progress["message"])
        self.assertNotIn("adoption_started", progress["message"])

        task_plan = TaskPlan([
            Task("write-a", "write one file", files=["src/feature.py"],
                 metadata={"execution_mode": "mutating"})
        ])
        ledger = EngineeringRecoveryLedger(state_root / "recovery.sqlite")
        recovery_journal = ImprovementJournal(state_root / "recovery.jsonl")
        bridge = GraphRunRecoveryBridge(
            run_id="graph-journal-crash-boundary", plan=task_plan,
            intent_digest=self.intent_digest, source_revision=git(self.repo, "rev-parse", "HEAD"),
            ledger=ledger, journal=recovery_journal,
        )
        snapshot = bridge.load()
        self.assertEqual(snapshot["nodes"]["write-a"]["status"], "adopting")
        self.assertEqual(snapshot["next_safe_action"], "reconcile_target_tree_and_recovery_ledger")
        self.assertEqual(bridge.dispatchable_nodes(), ())

        coordinator = GraphWorkspaceCoordinator(
            self.repo, workspace_root=state_root / "restart-workspaces"
        )
        verifier = IndependentGraphVerifier(
            self.repo, verifier_identity="verifier-ci", trusted_verifier_ids=("verifier-ci",),
            worktree_root=state_root / "restart-verification-workspaces",
        )
        restarted = GraphWorkerLifecycle(
            coordinator=coordinator, verifier=verifier, journal=progress_journal,
            task_plan=task_plan, intent_digest=self.intent_digest,
            runtime_guard=RuntimeExecutionGuard.resume(snapshot["budget_snapshot"]),
            recovery_bridge=bridge,
        )
        with self.assertRaisesRegex(PermissionError, "durable progress already exists"):
            restarted.prepare(
                task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
                owned_paths=["src/feature.py"], execute_in_workspace=lambda *_args: None,
                checks=(),
            )
        recovered = bridge.load()
        self.assertEqual(recovered["run_status"], "blocked")
        self.assertEqual(recovered["nodes"]["write-a"]["status"], "unknown")
        self.assertEqual(
            recovered["next_safe_action"],
            "reconcile_target_tree_and_recovery_ledger",
        )
        self.assertEqual(bridge.dispatchable_nodes(), ())
        self.assertFalse((self.repo / "src" / "feature.py").exists())


    def test_process_crash_after_terminal_progress_before_recovery_commit_blocks_restart(self):
        state_root = self.root / "terminal-progress-crash-state"
        state_root.mkdir()
        script = textwrap.dedent("""\
            import hashlib
            import os
            import sys
            from pathlib import Path
            from portable.agency_evolution_gates import ImprovementJournal
            from portable.engineering_recovery import EngineeringRecoveryLedger, GraphRunRecoveryBridge
            from portable.graph_worker_lifecycle import GraphWorkerLifecycle
            from portable.graph_workspace_adoption import GraphWorkspaceCoordinator
            from portable.independent_graph_verification import IndependentGraphVerifier
            from portable.progress_journal import ProgressJournal
            from portable.runtime_recovery import RuntimeExecutionGuard
            from portable.task_planner import Task, TaskPlan

            repo = Path(sys.argv[1])
            state_root = Path(sys.argv[2])
            intent_digest = hashlib.sha256(b"intent").hexdigest()
            task_plan = TaskPlan([
                Task("write-a", "write one file", files=["src/feature.py"],
                     metadata={"execution_mode": "mutating"})
            ])
            journal = ProgressJournal(state_root / "progress.jsonl")
            guard = RuntimeExecutionGuard(max_steps=20, max_attempts=20, deadline_seconds=60)
            coordinator = GraphWorkspaceCoordinator(
                repo, workspace_root=state_root / "worker-workspaces"
            )
            verifier = IndependentGraphVerifier(
                repo, verifier_identity="verifier-ci", trusted_verifier_ids=("verifier-ci",),
                worktree_root=state_root / "verification-workspaces",
            )
            ledger = EngineeringRecoveryLedger(state_root / "recovery.sqlite")
            recovery_journal = ImprovementJournal(state_root / "recovery.jsonl")
            bridge = GraphRunRecoveryBridge(
                run_id="graph-terminal-progress-crash", plan=task_plan,
                intent_digest=intent_digest, source_revision=coordinator.base_revision,
                ledger=ledger, journal=recovery_journal,
            )
            lifecycle = GraphWorkerLifecycle(
                coordinator=coordinator, verifier=verifier, journal=journal,
                task_plan=task_plan, intent_digest=intent_digest,
                runtime_guard=guard, recovery_bridge=bridge,
            )

            def write(workspace, _task):
                (workspace / "src" / "feature.py").write_text(
                    "verified = True\\n", encoding="utf-8"
                )

            check_code = (
                "from pathlib import Path; "
                "assert Path('src/feature.py').read_text(encoding='utf-8') == "
                + repr("verified = True\\n")
            )
            prepared = lifecycle.prepare(
                task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
                owned_paths=["src/feature.py"], execute_in_workspace=write,
                checks=({
                    "check_id": "content-check",
                    "argv": [sys.executable, "-c", check_code],
                    "timeout_seconds": 20,
                },),
            )
            original_record = journal.record

            def crash_after_terminal_progress(task_id, event, **kwargs):
                result = original_record(task_id, event, **kwargs)
                if event == "verified-proposal-adopted":
                    # Simulate process death after the terminal journal fsync,
                    # but before the recovery ledger can commit node completion.
                    os._exit(84)
                return result

            journal.record = crash_after_terminal_progress
            lifecycle.adopt_prepared((prepared.worker_id,))
            os._exit(87)
        """)
        child = subprocess.run(
            [sys.executable, "-c", script, str(self.repo), str(state_root)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=False, timeout=90,
        )
        self.assertEqual(child.returncode, 84, child.stdout + child.stderr)
        self.assertEqual(
            (self.repo / "src" / "feature.py").read_text(encoding="utf-8"),
            "verified = True\n",
        )

        journal = ProgressJournal(state_root / "progress.jsonl")
        progress_before = journal.snapshot("write-a")
        self.assertEqual(progress_before["state"], "completed")
        self.assertEqual(progress_before["stage"], "adoption")
        self.assertGreaterEqual(progress_before["event_count"], 1)

        task_plan = TaskPlan([
            Task("write-a", "write one file", files=["src/feature.py"],
                 metadata={"execution_mode": "mutating"})
        ])
        ledger = EngineeringRecoveryLedger(state_root / "recovery.sqlite")
        recovery_journal = ImprovementJournal(state_root / "recovery.jsonl")
        bridge = GraphRunRecoveryBridge(
            run_id="graph-terminal-progress-crash", plan=task_plan,
            intent_digest=self.intent_digest, source_revision=self.coordinator.base_revision,
            ledger=ledger, journal=recovery_journal,
        )
        snapshot = bridge.load()
        self.assertEqual(snapshot["run_status"], "in_progress")
        self.assertEqual(snapshot["nodes"]["write-a"]["status"], "adopting")
        self.assertEqual(
            snapshot["nodes"]["write-a"]["next_safe_action"],
            "reconcile_target_tree_and_recovery_ledger",
        )

        restarted = GraphWorkerLifecycle(
            coordinator=self.coordinator,
            verifier=self.verifier,
            journal=journal,
            task_plan=task_plan,
            intent_digest=self.intent_digest,
            runtime_guard=RuntimeExecutionGuard.resume(snapshot["budget_snapshot"]),
            recovery_bridge=bridge,
        )

        def must_not_redispatch(*_args):
            raise AssertionError("a crashed worker must not be replayed automatically")

        with self.assertRaisesRegex(PermissionError, "durable progress already exists"):
            restarted.prepare(
                task_id="write-a", worker_id="worker-a", worker_identity="worker-a",
                owned_paths=["src/feature.py"], execute_in_workspace=must_not_redispatch,
                checks=(),
            )

        reconciled = journal.snapshot("write-a")
        self.assertEqual(reconciled["state"], "blocked")
        self.assertEqual(reconciled["reported_state"], "blocked")
        self.assertIn("reconcile_target_tree_and_recovery_ledger", reconciled["message"])
        self.assertEqual(bridge.load()["nodes"]["write-a"]["status"], "unknown")
        self.assertEqual(bridge.load()["run_status"], "blocked")
        self.assertEqual(bridge.dispatchable_nodes(), ())


    def test_verifier_cannot_self_approve_its_own_proposal(self):
        plan = self.lifecycle([
            Task("write-a", "write one file", files=["src/feature.py"], metadata={"execution_mode": "mutating"})
        ])
        marker = self.root / "self-approval-check-ran"

        def write(workspace, _task):
            (workspace / "src" / "feature.py").write_text("verified = True\n", encoding="utf-8")

        check_code = (
            "from pathlib import Path; "
            f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')"
        )
        checks = ({
            "check_id": "self-approval-must-not-run",
            "argv": ["python", "-c", check_code],
            "timeout_seconds": 20,
        },)
        with self.assertRaisesRegex(PermissionError, "worker cannot verify its own proposal"):
            plan.prepare(
                task_id="write-a", worker_id="worker-a", worker_identity="verifier-ci",
                owned_paths=["src/feature.py"], execute_in_workspace=write,
                checks=checks,
            )

        self.assertFalse(marker.exists(), "the verifier must reject self-approval before running checks")
        self.assertFalse((self.repo / "src" / "feature.py").exists())
        self.assertEqual(self.journal.snapshot("write-a")["state"], "failed")
        recovery = plan.recovery_bridge.load()
        self.assertEqual(recovery["nodes"]["write-a"]["status"], "failed")
        self.assertEqual(recovery["run_status"], "blocked")


if __name__ == "__main__":
    unittest.main()
