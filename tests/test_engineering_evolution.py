from pathlib import Path
import tempfile
import unittest

from portable.engineering_evidence_envelope import EvidenceRef, EngineeringEvidenceEnvelope
from portable.engineering_evolution import EngineeringEvolutionControlPlane, PHASES
from portable.persistent_memory import PersistentMemory
from portable.task_planner import TaskPlan


class EngineeringEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = PersistentMemory(Path(self.tmp.name) / "memory.sqlite", require_approval=False)
        self.cp = EngineeringEvolutionControlPlane(self.memory, "test-project")

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_twelve_phases_are_exposed(self):
        self.assertEqual(len(PHASES), 12)
        self.assertEqual(tuple(self.cp.status()["phases"]), PHASES)

    def test_evidence_graph_is_derived_from_envelope(self):
        envelope = EngineeringEvidenceEnvelope(
            task_id="task-1", intent_digest="intent", repository_snapshot_digest="repo",
            evidence=(EvidenceRef("e1", "repo"),),
        )
        result = self.cp.evidence_graph(envelope)
        self.assertEqual(result.envelope_id, envelope.envelope_digest)
        self.assertGreaterEqual(len(result.node_ids), 4)
        self.assertTrue(result.graph_digest)

    def test_evidence_graph_integrity_is_fail_closed(self):
        envelope = EngineeringEvidenceEnvelope(
            task_id="task-1", intent_digest="intent", repository_snapshot_digest="repo",
            evidence=(EvidenceRef("e1", "repo"),),
            verification_ids=("v1",), review_ids=("r1",), regression_ids=("g1",), outcome_id="o1",
        )
        result = self.cp.evidence_graph(envelope)
        integrity = self.cp.validate_evidence_graph(envelope, result)
        self.assertTrue(integrity.valid)
        self.assertFalse(integrity.missing_nodes)

    def test_world_feedback_uses_verified_empirical_calibration(self):
        from types import SimpleNamespace
        class Model:
            def score_prediction(self, prediction, actual):
                return SimpleNamespace(prediction_id="p1", error_digest="err", absolute_match=True)
            def prediction_calibration(self, *, predicate=None, action=None):
                return {"samples": 4, "accuracy": 0.75}
        prediction = SimpleNamespace(prediction_id="p1", predicate="state", action="repair", confidence=0.9)
        result = self.cp.record_world_feedback(Model(), prediction, "ok", evidence=("observation-1",))
        self.assertEqual(result.sample_count, 4)
        self.assertAlmostEqual(result.prediction_accuracy, 0.75)
        self.assertAlmostEqual(result.calibrated_confidence, 0.825)
        with self.assertRaises(ValueError):
            self.cp.record_world_feedback(Model(), prediction, "ok")

    def test_aer_compaction_is_deterministic_and_bounded(self):
        items = [
            {"evidence_id": "e2", "confidence": 0.4, "value": "secondary"},
            {"evidence_id": "e1", "confidence": 0.9, "value": "primary"},
        ]
        a = self.cp.compact_context(items, budget=200)
        b = self.cp.compact_context(items, budget=200)
        self.assertEqual(a.digest, b.digest)
        self.assertLessEqual(len(a.representation), 200)

    def test_impact_gate_requires_approval_for_critical_shared_change(self):
        root = Path(self.tmp.name) / "impact"
        root.mkdir()
        (root / "shared.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "one.py").write_text("from shared import VALUE\n", encoding="utf-8")
        (root / "two.py").write_text("from shared import VALUE\n", encoding="utf-8")
        with self.assertRaises(PermissionError):
            self.cp.impact_gate(root, ["shared.py"])
        report = self.cp.impact_gate(root, ["shared.py"], critical_approved=True)
        self.assertTrue(report.review_required)

    def test_failure_prediction_uses_persistent_history(self):
        self.cp.backlog.upsert(
            finding_id="f1", task_family="coding", capability="testing", hat="quality",
            severity="high", title="Regression", detail="test gap",
            recommendation="add test", evidence_ids=("e1",), attempts_increment=2,
        )
        result = self.cp.failure_prediction(task_family="coding", capability="testing")
        self.assertEqual(result.sample_count, 1)
        self.assertGreater(result.probability, 0)

    def test_failure_gate_requires_approval_when_risk_is_high(self):
        self.cp.backlog.upsert(
            finding_id="f-gate", task_family="coding", capability="testing", hat="quality",
            severity="critical", title="Regression", detail="test gap",
            recommendation="add test", evidence_ids=("e1",), attempts_increment=3,
        )
        with self.assertRaises(PermissionError):
            self.cp.failure_gate(task_family="coding", capability="testing")
        prediction = self.cp.failure_gate(task_family="coding", capability="testing", approved=True)
        self.assertGreaterEqual(prediction.probability, 0.5)

    def test_historical_decomposition_is_dependency_safe(self):
        self.cp.backlog.upsert(
            finding_id="f1", task_family="coding", capability="testing", hat="quality",
            severity="high", title="A", detail="a", recommendation="fix a",
        )
        result = self.cp.historical_decomposition(task_family="coding", capability="testing")
        self.assertIsInstance(result.plan, TaskPlan)
        self.assertEqual(result.source_findings, ("f1",))

    def test_historical_decomposition_gate_requires_evidence(self):
        self.cp.backlog.upsert(
            finding_id="f-evidence", task_family="coding", capability="testing", hat="quality",
            severity="high", title="A", detail="a", recommendation="fix a", evidence_ids=("e1",),
        )
        result = self.cp.historical_decomposition_gate(task_family="coding", capability="testing")
        self.assertEqual(result.source_findings, ("f-evidence",))

    def test_provider_selection_uses_empirical_calibration(self):
        for _ in range(3):
            self.cp.record_provider_result("fast", "coding", success=True, duration_seconds=1, quality=0.9)
            self.cp.record_provider_result("slow", "coding", success=True, duration_seconds=5, quality=0.9)
        self.assertEqual(self.cp.select_provider(("fast", "slow"), "coding"), "fast")
        with self.assertRaises(LookupError):
            self.cp.select_provider(("unknown",), "coding")

    def test_provider_and_transfer_calibration(self):
        for success in (True, True, False):
            self.cp.record_provider_result("local", "coding", success=success, duration_seconds=2, quality=0.8)
        calibration = self.cp.provider_calibration("local", "coding")
        self.assertEqual(calibration.samples, 3)
        self.assertAlmostEqual(calibration.success_rate, 2 / 3, places=3)

        for success in (True, True, True, True, True):
            self.cp.record_transfer_result("p1", "p2", "testing", success=success)
        transfer = self.cp.cross_project_validation("p1", "p2", "testing")
        self.assertTrue(transfer.accepted)

    def test_cross_project_gate_requires_held_out_transfer(self):
        for _ in range(5):
            self.cp.record_transfer_result("source", "target", "testing", success=True)
        result = self.cp.cross_project_gate("source", "target", "testing")
        self.assertTrue(result.accepted)
        with self.assertRaises(ValueError):
            self.cp.cross_project_gate("source", "source", "testing")

    def test_benchmark_gate_and_local_readiness_are_fail_closed(self):
        class Result:
            success_rate = 0.95
            transfer_rate = 0.9
        self.assertTrue(self.cp.benchmark_gate(Result()))
        readiness = self.cp.local_execution_readiness()
        self.assertFalse(readiness.ready)

    def test_compaction_never_drops_required_evidence(self):
        required = {"evidence_id": "required", "confidence": 0.1, "required": True, "value": "must-keep" * 20}
        with self.assertRaises(ValueError):
            self.cp.compact_context([required], budget=128)
        result = self.cp.compact_context([required], budget=256)
        self.assertIn("required", result.representation)

    def test_change_impact_prediction(self):
        root = Path(self.tmp.name) / "repo"
        root.mkdir()
        (root / "a.py").write_text("x = 1\n", encoding="utf-8")
        report = self.cp.change_impact(root, ["a.py"])
        self.assertEqual(report.changed, ("a.py",))


if __name__ == "__main__":
    unittest.main()
