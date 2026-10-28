from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "benchmarks" / "capability_coverage_matrix.v1.json"
REQUIRED_FIELDS = {
    "id", "name", "owner", "status", "dependencies", "trusted_data_source",
    "runtime_identity", "evaluator_identity", "metrics", "test_protocol",
    "baseline", "held_out_plan", "threat_model", "exact_head_ci",
    "promotion_criteria", "external_dependencies",
}


class CapabilityCoverageMatrixTests(unittest.TestCase):
    def test_matrix_is_versioned_and_covers_required_capability_tracks(self):
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        self.assertEqual(matrix["schema_version"], "auren-capability-coverage-v1")
        self.assertIn("not a claim", matrix["purpose"])
        self.assertGreaterEqual(len(matrix["tracks"]), 9)
        track_ids = [track["id"] for track in matrix["tracks"]]
        self.assertEqual(len(track_ids), len(set(track_ids)))
        required = {
            "multimodal_grounded_action",
            "open_ended_environment_learning",
            "long_horizon_autonomy",
            "cross_domain_transfer",
            "causal_scientific_discovery",
            "memory_continual_learning",
            "planning_tools_collaboration",
            "calibration_robustness_adversarial",
            "independent_evaluation_governance",
        }
        self.assertTrue(required <= set(track_ids))

    def test_every_track_declares_baseline_holdout_threat_and_external_dependencies(self):
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        for track in matrix["tracks"]:
            with self.subTest(track=track["id"]):
                self.assertTrue(REQUIRED_FIELDS <= set(track))
                self.assertTrue(track["owner"].strip())
                self.assertTrue(track["trusted_data_source"].strip())
                self.assertTrue(track["runtime_identity"].strip())
                self.assertTrue(track["evaluator_identity"].strip())
                self.assertTrue(track["metrics"])
                self.assertTrue(track["test_protocol"]["command"].strip())
                self.assertTrue(track["baseline"]["status"].strip())
                self.assertTrue(track["held_out_plan"].strip())
                self.assertTrue(track["threat_model"])
                self.assertTrue(track["exact_head_ci"])
                self.assertTrue(track["promotion_criteria"].strip())
                self.assertTrue(track["external_dependencies"])

    def test_referenced_internal_test_modules_exist(self):
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        for track in matrix["tracks"]:
            command = track["test_protocol"]["command"]
            modules = [token.removeprefix("tests.").replace(".", "/") + ".py"
                       for token in command.split() if token.startswith("tests.test_")]
            with self.subTest(track=track["id"]):
                self.assertTrue(modules)
                for module in modules:
                    self.assertTrue((ROOT / "tests" / module.removeprefix("tests/")).is_file(), module)

    def test_living_radar_signals_are_actionable_and_fail_closed(self):
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        radar = matrix["radar_refresh"]
        self.assertTrue(radar["reviewed_at"])
        self.assertTrue(radar["baseline_main_sha"])
        self.assertIn("candidate signals only", radar["status"])
        self.assertGreaterEqual(len(radar["signals"]), 7)
        required = {
            "id", "source_date", "sources", "owner", "status",
            "implementation_status", "ci_status", "deployment_status",
            "independent_evaluation_status", "measured_gap", "benchmark",
            "acceptance_threshold", "rollback",
        }
        signal_ids = []
        for signal in radar["signals"]:
            with self.subTest(signal=signal.get("id")):
                self.assertTrue(required <= set(signal))
                signal_ids.append(signal["id"])
                self.assertTrue(signal["owner"])
                self.assertTrue(signal["sources"])
                self.assertTrue(all(url.startswith("https://") for url in signal["sources"]))
                self.assertTrue(signal["measured_gap"].strip())
                self.assertTrue(signal["benchmark"].strip())
                self.assertTrue(signal["acceptance_threshold"].strip())
                self.assertTrue(signal["rollback"].strip())
                self.assertTrue(signal["deployment_status"].strip())
                self.assertTrue(signal["independent_evaluation_status"].strip())
        self.assertEqual(len(signal_ids), len(set(signal_ids)))
        self.assertIn("not verified", {s["deployment_status"] for s in radar["signals"]})
        self.assertIn("blocked", {s["independent_evaluation_status"] for s in radar["signals"]})

    def test_matrix_does_not_define_a_numeric_superintelligence_score(self):
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("superintelligence_score", matrix)
        self.assertTrue(any("No aggregate superintelligence score" in rule for rule in matrix["global_rules"]))


if __name__ == "__main__":
    unittest.main()
