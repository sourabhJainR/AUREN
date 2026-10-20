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
from portable.persistent_memory import PersistentMemory


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
