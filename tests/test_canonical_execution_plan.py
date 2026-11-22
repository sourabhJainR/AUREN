"""End-to-end tests for canonical plan admission and write-boundary enforcement."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.canonical_execution_plan import (
    CanonicalExecutionPlan, execution_plan_scope,
)
from portable.execution_controller import ExecutionController
from portable.guarded_filesystem import write_workspace_text


def _plan_write_handler(payload):
    write_workspace_text(payload["root"], payload["path"], "plan-scoped write")
    return {"success": True, "detail": "written", "evidence": ["workspace-write"]}


class CanonicalExecutionPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-b", "main", str(self.root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "AUREN Tests"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "auren-tests@example.invalid"], check=True)
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-m", "fixture"], check=True, capture_output=True)
        self.head = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.env = patch.dict(os.environ, {"AUREN_REPOSITORY_SLUG": "acme/fileoperations"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def plan(self, allowed_paths=("output/**",)):
        return {
            "target": {
                "repository": "acme/fileoperations",
                "branch": "main",
                "base_sha": self.head,
                "allowed_paths": list(allowed_paths),
                "governance_repository": "acme/auren",
            },
            "requirements": ["REQ-1"],
            "acceptance": ["Output is written only inside the approved path."],
            "required_capabilities": ["workspace_filesystem"],
            "capabilities": [{
                "name": "workspace_filesystem",
                "available": True,
                "evidence": "guarded-filesystem integration test",
            }],
            "stopping_condition": "All required checks pass or execution is blocked.",
        }

    def test_plan_digest_is_stable_and_runtime_identity_is_exact(self):
        plan = CanonicalExecutionPlan.from_mapping(self.plan())
        self.assertEqual(plan.digest, CanonicalExecutionPlan.from_mapping(plan.payload()).digest)
        plan.validate_runtime(repository="acme/fileoperations", branch="main", head_sha=self.head)
        with self.assertRaises(PermissionError):
            plan.validate_runtime(repository="acme/other", branch="main", head_sha=self.head)
        with self.assertRaises(PermissionError):
            plan.validate_runtime(repository="acme/fileoperations", branch="main", head_sha="b" * 40)

    def test_guarded_filesystem_enforces_plan_scope(self):
        plan = CanonicalExecutionPlan.from_mapping(self.plan())
        with execution_plan_scope(plan):
            write_workspace_text(self.root, "output/result.txt", "allowed")
            with self.assertRaises(PermissionError):
                write_workspace_text(self.root, "secrets/result.txt", "denied")
        self.assertEqual((self.root / "output/result.txt").read_text(encoding="utf-8"), "allowed")
        self.assertFalse((self.root / "secrets/result.txt").exists())

    def test_controller_binds_plan_and_allows_scoped_write(self):
        controller = ExecutionController(self.root)
        controller.register_handler("plan_write", _plan_write_handler, execution_class="read_only")
        task = controller.submit(
            "plan_write",
            {"root": str(self.root), "path": "output/result.txt"},
            execution_plan=self.plan(),
        )
        controller.run_once(task.id)
        self.assertEqual((self.root / "output/result.txt").read_text(encoding="utf-8"), "plan-scoped write")
        self.assertEqual(controller.task(task.id).state, "completed")

    def test_controller_blocks_out_of_scope_write_before_file_creation(self):
        controller = ExecutionController(self.root)
        controller.register_handler("plan_write", _plan_write_handler, execution_class="read_only")
        task = controller.submit(
            "plan_write",
            {"root": str(self.root), "path": "outside/result.txt"},
            execution_plan=self.plan(),
        )
        with self.assertRaises(PermissionError):
            controller.run_once(task.id)
        self.assertFalse((self.root / "outside/result.txt").exists())
        self.assertEqual(controller.task(task.id).state, "failed")

    def test_unknown_tool_schema_constraint_fails_closed_at_admission(self):
        plan = self.plan()
        plan["tool_contracts"] = [{
            "name": "write_file",
            "schema": {"type": "object", "properties": {"path": {"type": "string", "format": "uri"}}},
            "arguments": {"path": "output/result.txt"},
        }]
        with self.assertRaises(Exception):
            CanonicalExecutionPlan.from_mapping(plan).validate_runtime(
                repository="acme/fileoperations", branch="main", head_sha=self.head,
            )


if __name__ == "__main__":
    unittest.main()
