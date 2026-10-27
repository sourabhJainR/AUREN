from __future__ import annotations

import hashlib
import subprocess
import sys
import textwrap
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from portable.agency_evolution_gates import ImprovementJournal
from portable.engineering_recovery import EngineeringRecoveryLedger, GraphRunRecoveryBridge
from portable.graph_worker_lifecycle import GraphWorkerLifecycle
from portable.graph_workspace_adoption import GraphWorkspaceCoordinator
from portable.independent_graph_verification import IndependentGraphVerifier
from portable.progress_journal import ProgressJournal
from portable.runtime_recovery import RuntimeExecutionGuard
from portable.task_planner import Task, TaskPlan


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True,
        check=False, shell=False, timeout=20,
    )
    if result.returncode:
        raise AssertionError(f"git {args!r} failed: {result.stderr}")
    return result.stdout.strip()


def init_repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "config", "user.name", "AUREN Test")
    git(root, "config", "user.email", "auren-test@example.invalid")
    (root / "README.md").write_text("# seed\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", "README.md", "src/base.py")
    git(root, "commit", "-qm", "initial")
    return git(root, "rev-parse", "HEAD")


class GraphWorkspaceAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.base = init_repo(self.repo)
        self.coordinator = GraphWorkspaceCoordinator(
            self.repo, workspace_root=self.root / "isolated-workspaces"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_worker_gets_separate_worktree_and_source_is_unchanged(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        self.assertNotEqual(workspace.resolve(), self.repo.resolve())
        self.assertEqual(git(workspace, "rev-parse", "HEAD"), self.base)
        (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
        self.assertFalse((self.repo / "src" / "feature.py").exists())
        proposal = self.coordinator.prepare_proposal("worker-a")
        self.assertEqual(proposal.paths, ("src/feature.py",))
        self.assertTrue(Path(proposal.artifact_path).is_file())

    def test_overlapping_file_ownership_is_rejected_before_second_worktree(self):
        self.coordinator.create_worker("worker-a", ["src"])
        with self.assertRaisesRegex(PermissionError, "overlaps"):
            self.coordinator.create_worker("worker-b", ["src/feature.py"])
        self.assertNotIn("worker-b", self.coordinator.worker_states())

    def test_worker_cannot_propose_changes_outside_declared_scope(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "README.md").write_text("outside scope\n", encoding="utf-8")
        with self.assertRaisesRegex(PermissionError, "outside declared ownership"):
            self.coordinator.prepare_proposal("worker-a")
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "failed")
        artifacts = list((self.root / "isolated-workspaces" / ".proposals").glob("worker-a-*.patch"))
        self.assertEqual(len(artifacts), 1)
        self.coordinator.cleanup_worker("worker-a")
        self.assertFalse(workspace.exists())

    def test_adoption_checks_artifact_and_applies_only_owned_paths(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        adopted = self.coordinator.adopt(proposal)
        self.assertEqual(adopted, ("src/feature.py",))
        self.assertEqual((self.repo / "src" / "feature.py").read_text(encoding="utf-8"), "answer = 42\n")
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "adopted")

    def test_disjoint_sibling_proposals_are_adopted_in_one_serialized_step(self):
        workspace_a = self.coordinator.create_worker("worker-a", ["src/feature_a.py"])
        workspace_b = self.coordinator.create_worker("worker-b", ["src/feature_b.py"])
        (workspace_a / "src" / "feature_a.py").write_text("answer_a = 1\\n", encoding="utf-8")
        (workspace_b / "src" / "feature_b.py").write_text("answer_b = 2\\n", encoding="utf-8")
        proposal_a = self.coordinator.prepare_proposal("worker-a")
        proposal_b = self.coordinator.prepare_proposal("worker-b")
        adopted = self.coordinator.adopt_many((proposal_a, proposal_b))
        self.assertEqual(adopted, {
            "worker-a": ("src/feature_a.py",),
            "worker-b": ("src/feature_b.py",),
        })
        self.assertEqual((self.repo / "src" / "feature_a.py").read_text(encoding="utf-8"), "answer_a = 1\\n")
        self.assertEqual((self.repo / "src" / "feature_b.py").read_text(encoding="utf-8"), "answer_b = 2\\n")

    def test_batch_adoption_rejects_duplicate_proposals_before_apply(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        with self.assertRaisesRegex(ValueError, "worker ids must be unique"):
            self.coordinator.adopt_many((proposal, proposal))
        self.assertFalse((self.repo / "src" / "feature.py").exists())

    def test_failed_post_apply_verification_rolls_back_target(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        import portable.graph_workspace_adoption as adoption_module
        real_git = adoption_module._git
        applied = {"value": False}

        def injected_git(root, *args, input_bytes=None):
            if args[:2] == ("apply", "--") and "--check" not in args:
                result = real_git(root, *args, input_bytes=input_bytes)
                applied["value"] = True
                return result
            if applied["value"] and args[:2] == ("diff", "--name-only"):
                return b"unexpected\x00"
            return real_git(root, *args, input_bytes=input_bytes)

        with patch("portable.graph_workspace_adoption._git", side_effect=injected_git):
            with self.assertRaisesRegex(RuntimeError, "adopted path set differs"):
                self.coordinator.adopt(proposal)
        self.assertFalse((self.repo / "src" / "feature.py").exists())
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "failed")

    def test_stale_target_head_blocks_adoption(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        (self.repo / "README.md").write_text("# changed base\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-qm", "advance target")
        with self.assertRaisesRegex(RuntimeError, "stale target HEAD"):
            self.coordinator.adopt(proposal)
        self.assertFalse((self.repo / "src" / "feature.py").exists())

    def test_dirty_target_blocks_adoption_before_any_patch_is_applied(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        (self.repo / "README.md").write_text("# local edit\n", encoding="utf-8")
        with self.assertRaisesRegex(PermissionError, "not clean"):
            self.coordinator.adopt(proposal)
        self.assertFalse((self.repo / "src" / "feature.py").exists())

    def test_failed_adoption_cancels_siblings_and_records_failure(self):
        workspace_a = self.coordinator.create_worker("worker-a", ["src/a.py"])
        self.coordinator.create_worker("worker-b", ["src/b.py"])
        (workspace_a / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        (self.repo / "README.md").write_text("# dirty target\n", encoding="utf-8")
        with self.assertRaisesRegex(PermissionError, "not clean"):
            self.coordinator.adopt(proposal)
        states = self.coordinator.worker_states()
        self.assertEqual(states["worker-a"], "failed")
        self.assertEqual(states["worker-b"], "cancel_requested")
        self.assertTrue(Path(proposal.artifact_path).is_file())
        self.coordinator.cleanup_worker("worker-a")
        self.coordinator.mark_worker_stopped("worker-b")
        self.coordinator.cleanup_worker("worker-b")

    def test_cancellation_blocks_new_workers_and_does_not_claim_sibling_stopped(self):
        self.coordinator.create_worker("worker-a", ["src/a.py"])
        states = self.coordinator.cancel_siblings("hard safety failure")
        self.assertEqual(states, {"worker-a": "cancel_requested"})
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "cancel_requested")
        with self.assertRaisesRegex(RuntimeError, "hard safety failure"):
            self.coordinator.create_worker("worker-b", ["src/b.py"])
        self.coordinator.mark_worker_stopped("worker-a")
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "stopped")

    def test_cleanup_refuses_live_worker_and_removes_captured_proposal_safely(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "may still be running"):
            self.coordinator.cleanup_worker("worker-a")
        proposal = self.coordinator.prepare_proposal("worker-a")
        removed = self.coordinator.cleanup_worker("worker-a")
        self.assertEqual(Path(removed), workspace)
        self.assertFalse(workspace.exists())
        self.assertTrue(Path(proposal.artifact_path).is_file())
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "cleaned")


    def test_process_death_inside_git_apply_blocks_resume_until_reconciliation(self):
        self.verifier = IndependentGraphVerifier(
            self.repo, verifier_identity="verifier-ci", trusted_verifier_ids=("verifier-ci",),
            worktree_root=self.root / "parent-verification-workspaces",
        )
        self.intent_digest = hashlib.sha256(b"intent").hexdigest()
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
