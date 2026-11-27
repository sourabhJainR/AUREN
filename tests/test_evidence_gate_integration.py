"""Regression tests for durable evidence-gate integration."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from portable.evidence_gate import EvidenceCheck
from portable.evidence_gate_integration import EvidenceGateIntegration
from portable.execution_controller import ExecutionController
from portable.engineering_recovery import EngineeringRecoveryLedger
from portable.persistent_memory import PersistentMemory

WORKFLOW = ".github/workflows/browser-qa.yml"


def browser_receipt(sha: str, *, state: str = "passed", repository: str = "owner/repo",
                    observed_sha: str | None = None, observed_path: str | None = WORKFLOW) -> dict:
    return {
        "provider": "github-actions",
        "repository": repository,
        "run_id": 42,
        "run_url": "https://github.com/owner/repo/actions/runs/42",
        "expected_sha": sha,
        "observed_sha": sha if observed_sha is None else observed_sha,
        "expected_workflow_path": WORKFLOW,
        "observed_workflow_path": observed_path,
        "status": "completed",
        "conclusion": "success",
        "state": state,
    }


class EvidenceGateIntegrationTests(unittest.TestCase):
    def test_receipt_and_check_lineage_are_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            memory_path = Path(directory) / "memory.sqlite"
            memory = PersistentMemory(memory_path, require_approval=False)
            integration = EvidenceGateIntegration(memory, "integration-test")
            runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""))
            result = integration.run_and_record(
                directory,
                run_id="run-1",
                expected_revision="abc123",
                checks=(EvidenceCheck("unit", ("python", "-m", "pytest"), 5),),
                command_runner=runner,
                revision_reader=lambda _: "abc123",
            )
            self.assertEqual(result.receipt.status, "PASS")
            graph = integration.graph
            receipt_node = graph.get_node(result.receipt_node_id)
            self.assertIsNotNone(receipt_node)
            receipt_metadata = dict(receipt_node.metadata)
            self.assertEqual(receipt_metadata["status"], "PASS")
            self.assertEqual(json.loads(receipt_metadata["receipt_json"])["digest"], result.receipt.digest)
            run_node = graph.get_node(result.run_node_id)
            self.assertEqual(dict(run_node.metadata)["run_id"], "run-1")
            self.assertEqual(len(result.check_node_ids), 1)
            check_node = graph.get_node(result.check_node_ids[0])
            check_metadata = dict(check_node.metadata)
            self.assertEqual(check_metadata["status"], "PASS")
            self.assertEqual(json.loads(check_metadata["result_json"])["stdout"], "ok")
            self.assertEqual(len(graph.lineage(result.receipt_node_id, direction="both")), 2)
            memory.close()

            reopened = PersistentMemory(memory_path, require_approval=False)
            recovered = EvidenceGateIntegration(reopened, "integration-test")
            self.assertIsNotNone(recovered.graph.get_node(result.run_node_id))
            self.assertIsNotNone(recovered.graph.get_node(result.receipt_node_id))
            self.assertIsNotNone(recovered.graph.get_node(result.check_node_ids[0]))
            reopened.close()

    def test_browser_ci_receipt_is_exact_head_bound_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            memory_path = Path(directory) / "memory.sqlite"
            memory = PersistentMemory(memory_path, require_approval=False)
            integration = EvidenceGateIntegration(memory, "owner/repo", expected_repository="owner/repo")
            sha = "a" * 40
            receipt = browser_receipt(sha)
            recorded = integration.record_browser_qa_ci_receipt(receipt, expected_revision=sha)
            node = integration.graph.get_node(recorded["receipt_node_id"])
            self.assertIsNotNone(node)
            metadata = dict(node.metadata)
            self.assertEqual(metadata["authorization"], "none")
            self.assertEqual(metadata["expected_repository"], "owner/repo")
            self.assertEqual(json.loads(metadata["receipt_json"]), receipt)
            with self.assertRaises(ValueError):
                integration.record_browser_qa_ci_receipt(receipt, expected_revision="b" * 40)
            with self.assertRaises(ValueError):
                integration.record_browser_qa_ci_receipt(
                    {**receipt, "observed_sha": "b" * 40}, expected_revision=sha,
                )
            with self.assertRaises(ValueError):
                integration.record_browser_qa_ci_receipt(
                    {**receipt, "repository": "other/repo"}, expected_revision=sha,
                )
            memory.close()
            reopened = PersistentMemory(memory_path, require_approval=False)
            recovered = EvidenceGateIntegration(reopened, "owner/repo", expected_repository="owner/repo")
            self.assertIsNotNone(recovered.graph.get_node(recorded["run_node_id"]))
            self.assertIsNotNone(recovered.graph.get_node(recorded["receipt_node_id"]))
            reopened.close()

    def test_invalid_repository_slug_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.sqlite", require_approval=False)
            with self.assertRaisesRegex(ValueError, "valid repository slug"):
                EvidenceGateIntegration(memory, "project", expected_repository="../repo")
            memory.close()

    def test_receipt_fails_closed_without_configured_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.sqlite", require_approval=False)
            integration = EvidenceGateIntegration(memory, "unconfigured")
            with self.assertRaisesRegex(ValueError, "expected repository is not configured"):
                integration.record_browser_qa_ci_receipt(browser_receipt("a" * 40), expected_revision="a" * 40)
            memory.close()

    def test_nonpassed_sha_and_workflow_mismatch_receipts_are_recordable(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.sqlite", require_approval=False)
            integration = EvidenceGateIntegration(memory, "owner/repo", expected_repository="owner/repo")
            sha = "a" * 40
            sha_mismatch = browser_receipt(sha, state="sha_mismatch", observed_sha="b" * 40)
            recorded_sha = integration.record_browser_qa_ci_receipt(sha_mismatch, expected_revision=sha)
            self.assertEqual(recorded_sha["state"], "sha_mismatch")
            workflow_mismatch = browser_receipt(
                sha, state="workflow_mismatch", observed_path=".github/workflows/test.yml",
            )
            recorded_workflow = integration.record_browser_qa_ci_receipt(workflow_mismatch, expected_revision=sha)
            self.assertEqual(recorded_workflow["state"], "workflow_mismatch")
            memory.close()

    def test_pass_requires_exact_workflow_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.sqlite", require_approval=False)
            integration = EvidenceGateIntegration(memory, "owner/repo", expected_repository="owner/repo")
            receipt = browser_receipt("a" * 40, observed_path=".github/workflows/test.yml")
            with self.assertRaisesRegex(ValueError, "expected workflow identity"):
                integration.record_browser_qa_ci_receipt(receipt, expected_revision="a" * 40)
            memory.close()

    def test_controller_checkpoints_exact_head_ci_and_recovers_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory_path = root / "memory.sqlite"
            memory = PersistentMemory(memory_path, require_approval=False)
            controller = ExecutionController.__new__(ExecutionController)
            controller.evidence_gate_integration = EvidenceGateIntegration(
                memory, "owner/repo", expected_repository="owner/repo",
            )
            recovery_path = root / "recovery.sqlite"
            recovery = EngineeringRecoveryLedger(recovery_path)
            recovery.start_run("browser-task", "verify browser QA exact head")
            sha = "c" * 40
            receipt = browser_receipt(sha)
            receipt["run_id"] = 99
            receipt["run_url"] = "https://github.com/owner/repo/actions/runs/99"
            result = controller.record_browser_qa_ci_receipt(
                receipt, expected_revision=sha, recovery_ledger=recovery, task_id="browser-task",
            )
            self.assertEqual(result["recovery_status"], "verified")
            checkpoint = EngineeringRecoveryLedger(recovery_path).get_checkpoint("browser-task", "browser_qa_ci")
            self.assertEqual(checkpoint.status, "verified")
            self.assertEqual(checkpoint.head_sha, sha)
            self.assertEqual(checkpoint.payload["verified_head_sha"], sha)
            memory.close()

    def test_stale_revision_is_recorded_and_checks_do_not_run(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.sqlite", require_approval=False)
            integration = EvidenceGateIntegration(memory, "integration-test")
            runner = Mock()
            result = integration.run_and_record(
                directory,
                run_id="run-stale",
                expected_revision="expected",
                checks=(EvidenceCheck("unit", ("python", "-m", "pytest"), 5),),
                command_runner=runner,
                revision_reader=lambda _: "actual",
            )
            self.assertEqual(result.receipt.status, "STALE")
            runner.assert_not_called()
            node = integration.graph.get_node(result.receipt_node_id)
            self.assertEqual(dict(node.metadata)["status"], "STALE")
            self.assertEqual(result.check_node_ids, ())
            memory.close()


if __name__ == "__main__":
    unittest.main()
