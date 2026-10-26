from __future__ import annotations

import hashlib
import subprocess
import sys
import time
import uuid
import tempfile
import unittest
from pathlib import Path

from portable.graph_workspace_adoption import GraphWorkspaceCoordinator
from portable.engineering_recovery import EngineeringRecoveryLedger, GraphRunRecoveryBridge
from portable.agency_evolution_gates import ImprovementJournal
from portable.graph_worker_lifecycle import GraphWorkerLifecycle
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


if __name__ == "__main__":
    unittest.main()
