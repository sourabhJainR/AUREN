from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from portable.agency_evolution_gates import ImprovementJournal
from portable.engineering_recovery import EngineeringRecoveryLedger, GraphRunRecoveryBridge
from portable.graph_worker_lifecycle import GraphWorkerLifecycle
from portable.graph_workspace_adoption import GraphWorkspaceCoordinator
from portable.independent_graph_verification import IndependentGraphVerifier
from portable.progress_journal import ProgressJournal
from portable.runtime_recovery import RuntimeExecutionGuard
from portable.task_planner import Task, TaskPlan


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
        shell=False,
        timeout=20,
    )
    if result.returncode:
        raise AssertionError(f"git {args!r} failed: {result.stderr}")
    return result.stdout.strip()


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.name", "AUREN Test")
    _git(root, "config", "user.email", "auren-test@example.invalid")
    (root / "README.md").write_text("# seed\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "README.md", "src/base.py")
    _git(root, "commit", "-qm", "initial")


class GraphApplyCrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        _init_repo(self.repo)
        self.coordinator = GraphWorkspaceCoordinator(
            self.repo, workspace_root=self.root / "parent-workspaces"
        )
        self.verifier = IndependentGraphVerifier(
            self.repo,
            verifier_identity="verifier-ci",
            trusted_verifier_ids=("verifier-ci",),
            worktree_root=self.root / "parent-verification-workspaces",
        )
        self.intent_digest = hashlib.sha256(b"intent").hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def test_process_death_inside_git_apply_blocks_resume_until_reconciliation(self):
        state_root = self.root / "crash-during-apply"
        state_root.mkdir()
        script = textwrap.dedent("""\
            import hashlib
            import os
            import sys
            from pathlib import Path
            import portable.graph_workspace_adoption as adoption_module
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
                Task(
                    "write-a", "write one file", files=["src/feature.py"],
                    metadata={"execution_mode": "mutating"},
                )
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
                run_id="graph-crash-inside-apply", plan=task_plan,
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
                    "verified = True\\\\n", encoding="utf-8"
                )

            check_code = (
                "from pathlib import Path; "
                "assert Path('src/feature.py').read_text(encoding='utf-8') == "
                + repr("verified = True\\\\n")
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
            original_git = adoption_module._git

            def crash_after_apply(root, *args, input_bytes=None):
                result = original_git(root, *args, input_bytes=input_bytes)
                if args[:2] == ("apply", "--") and "--check" not in args:
                    # Die after git apply returns but before adopt_many can mark
                    # the operation as applied or enter its rollback/finalize path.
                    os._exit(85)
                return result

            adoption_module._git = crash_after_apply
            lifecycle.adopt_prepared((prepared.worker_id,))
            os._exit(86)
        """)
        child = subprocess.run(
            [sys.executable, "-c", script, str(self.repo), str(state_root)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
        self.assertEqual(child.returncode, 85, child.stdout + child.stderr)
        self.assertEqual(
            (self.repo / "src" / "feature.py").read_text(encoding="utf-8"),
            "verified = True\n",
        )

        progress = ProgressJournal(state_root / "progress.jsonl").snapshot("write-a")
        self.assertEqual(progress["state"], "running")
        self.assertEqual(progress["stage"], "adoption")
        self.assertIn("reconcile_target_tree_and_recovery_ledger", progress["message"])

        task_plan = TaskPlan([
            Task(
                "write-a", "write one file", files=["src/feature.py"],
                metadata={"execution_mode": "mutating"},
            )
        ])
        bridge = GraphRunRecoveryBridge(
            run_id="graph-crash-inside-apply",
            plan=task_plan,
            intent_digest=self.intent_digest,
            source_revision=self.coordinator.base_revision,
            ledger=EngineeringRecoveryLedger(state_root / "recovery.sqlite"),
            journal=ImprovementJournal(state_root / "recovery.jsonl"),
        )
        snapshot = bridge.load()
        self.assertEqual(snapshot["run_status"], "in_progress")
        self.assertEqual(snapshot["nodes"]["write-a"]["status"], "adopting")
        self.assertEqual(snapshot["nodes"]["write-a"]["next_safe_action"], "reconcile_target_tree_and_recovery_ledger")
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
        self.assertEqual(recovered["nodes"]["write-a"]["status"], "unknown")
        self.assertEqual(
            recovered["next_safe_action"],
            "reconcile_target_tree_and_recovery_ledger",
        )
        with self.assertRaisesRegex(PermissionError, "source checkout must be clean"):
            GraphWorkspaceCoordinator(
                self.repo, workspace_root=state_root / "restart-workspaces"
            )


if __name__ == "__main__":
    unittest.main()
