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
from portable.bounded_subprocess import run_bounded_command


def _plan_write_handler(payload):
    write_workspace_text(payload["root"], payload["path"], "plan-scoped write")
    criteria = payload["_auren_execution_plan"]["acceptance"]
    return {
        "success": True, "detail": "written", "evidence": ["workspace-write"],
        "acceptance_evidence": {criterion: ["workspace-write"] for criterion in criteria},
    }


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

        with self.assertRaises(PermissionError):
            plan.validate_runtime(
                repository="acme/fileoperations", branch="main", head_sha=self.head,
                host_capabilities={"workspace_filesystem": False},
            )
        with self.assertRaises(PermissionError):
            plan.validate_runtime(
                repository="acme/fileoperations", branch="main", head_sha=self.head,
                host_capabilities={},
            )

    def test_guarded_filesystem_enforces_plan_scope(self):
        plan = CanonicalExecutionPlan.from_mapping(self.plan())
        with execution_plan_scope(plan):
            write_workspace_text(self.root, "output/result.txt", "allowed")
            with self.assertRaises(PermissionError):
                write_workspace_text(self.root, "secrets/result.txt", "denied")
        self.assertEqual((self.root / "output/result.txt").read_text(encoding="utf-8"), "allowed")
        self.assertFalse((self.root / "secrets/result.txt").exists())

    def test_plan_scoped_work_denies_raw_subprocess(self):
        plan = CanonicalExecutionPlan.from_mapping(self.plan())
        with execution_plan_scope(plan):
            with self.assertRaises(PermissionError):
                run_bounded_command(["python", "-c", "print('must not run')"], timeout_seconds=2)

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
        acceptance = controller.evidence_ledger.resolve(
            f"acceptance:{task.id}:0", repository="acme/fileoperations", head_sha=self.head,
        )
        self.assertIsNotNone(acceptance)
        self.assertEqual(acceptance["evidence_type"], "execution_acceptance")
        self.assertEqual(acceptance["status"], "unknown")  # no trusted signer configured

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

    def test_required_ci_checks_need_trusted_provider_and_exact_head(self):
        controller = ExecutionController(self.root)
        controller.register_handler("plan_write", _plan_write_handler, execution_class="read_only")
        plan = self.plan()
        plan["required_checks"] = ["unit"]
        plan["required_capabilities"].append("ci")
        plan["capabilities"].append({
            "name": "ci", "available": True, "evidence": "CI provider configured",
        })
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaises(PermissionError):
                controller.submit(
                    "plan_write", {"root": str(self.root), "path": "output/result.txt"},
                    execution_plan=plan,
                )

    def test_tool_contract_requires_trusted_registry_schema(self):
        controller = ExecutionController(self.root)
        controller.register_handler("plan_write", _plan_write_handler, execution_class="read_only")
        plan = self.plan()
        plan["tool_contracts"] = [{
            "name": "workspace.write",
            "schema": {
                "type": "object", "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            "arguments": {"path": "output/result.txt"},
        }]
        with self.assertRaises(PermissionError):
            controller.submit(
                "plan_write", {"root": str(self.root), "path": "output/result.txt"},
                execution_plan=plan,
            )

    def test_infeasible_requirements_are_rejected_before_task_creation(self):
        controller = ExecutionController(self.root)
        calls = []
        controller.register_handler(
            "plan_write",
            lambda payload: calls.append("ran") or _plan_write_handler(payload),
            execution_class="read_only",
        )
        cases = (
            (
                ("Build live market quotes.", "Use a free provider with 15-minute delayed quotes.", "Show delayed data as real-time."),
                (),
            ),
            (
                ("Provide durable historical price analytics.",),
                ("Do not store external market data.",),
            ),
            (
                ("Fetch required live data from an external API.",),
                ("The application must work offline with no network access.",),
            ),
            (
                ("Run every action fully autonomously without human approval.",),
                ("A human must approve every consequential action.",),
            ),
        )
        for index, (requirements, constraints) in enumerate(cases):
            with self.subTest(case=index):
                plan = self.plan()
                plan["requirements"] = list(requirements)
                plan["constraints"] = list(constraints)
                work_key = f"infeasible-plan-{index}"
                with self.assertRaises(PermissionError):
                    controller.submit(
                        "plan_write",
                        {"root": str(self.root), "path": f"output/rejected-{index}.txt"},
                        work_key=work_key,
                        execution_plan=plan,
                    )
                self.assertIsNone(controller.task_by_work_key(work_key))
        self.assertEqual(calls, [])
        self.assertFalse(any(self.root.glob("output/rejected-*.txt")))

    def test_sensitive_data_transfer_requires_trusted_human_approval_gate(self):
        controller = ExecutionController(self.root)
        controller.register_handler("plan_write", _plan_write_handler, execution_class="read_only")
        plan = self.plan()
        plan["constraints"] = [
            "Do not transfer personal or confidential data to external services without explicit human approval."
        ]
        work_key = "privacy-transfer-requires-approval"
        with self.assertRaises(PermissionError, match="approval_gated"):
            controller.submit(
                "plan_write",
                {"root": str(self.root), "path": "output/private-export.txt"},
                work_key=work_key,
                execution_plan=plan,
            )
        self.assertIsNone(controller.task_by_work_key(work_key))
        self.assertFalse((self.root / "output/private-export.txt").exists())

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
