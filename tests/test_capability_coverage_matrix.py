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

    def test_scientific_discovery_radar_tracks_recent_lifecycle_reviews(self):
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        signal = next(item for item in matrix["radar_refresh"]["signals"]
                      if item["id"] == "scientific_discovery")
        sources = set(signal["sources"])
        self.assertIn("https://www.nature.com/articles/s43586-026-00534-y", sources)
        self.assertIn("https://www.mdpi.com/3043-0097/1/2/8", sources)
        self.assertIn("https://openai.com/index/introducing-the-agents-api/",
                      {url for item in matrix["radar_refresh"]["signals"]
                       if item["id"] == "long_running_host_sandbox" for url in item["sources"]})
        self.assertIn("independent replication", signal["benchmark"].lower())
        self.assertIn("not validation", signal["acceptance_threshold"].lower())

    def test_matrix_does_not_define_a_numeric_superintelligence_score(self):
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("superintelligence_score", matrix)
        self.assertTrue(any("No aggregate superintelligence score" in rule for rule in matrix["global_rules"]))

    def test_2026_11_08_radar_refresh_tracks_current_gaps_without_promotion(self):
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        radar = matrix["radar_refresh"]
        self.assertEqual(radar["reviewed_at"], "2026-11-08")
        self.assertEqual(radar["baseline_main_sha"], "f5739d5f809a76d0d52f2138422ea52124eda5e5")
        signals = {item["id"]: item for item in radar["signals"]}
        required_sources = {
            "long_running_host_sandbox": "https://www.tomshardware.com/tech-industry/artificial-intelligence/openais-isolated-redteam-ai-agents-built-a-hidden-cpu-cache-channel-to-talk-with-no-network-or-shared-files-exploited-cpu-cache-timing-to-bypass-microvm-sandboxes",
            "dynamic_workflows_computer_use": "https://arxiv.org/abs/2611.07307",
            "adaptive_model_lifecycle": "https://metr.org/blog/2026-11-05-anthropic-ai-rd-acceleration-assessment/",
            "realistic_agent_evaluation": "https://arxiv.org/abs/2611.08134",
            "agent_coordination_action_oversight": "https://arxiv.org/abs/2611.07483",
            "agent_identity_delegation_and_action_oversight": "https://www.reuters.com/business/finance/us-bank-regulator-issues-first-ever-ai-guidance-for-examiners-2026-11-04/",
            "scientific_hypothesis_and_mechanism_validation": "https://doi.org/10.1109/TPAMI.2026.3712056",
            "event_driven_monitoring_agents": "https://www.microsoft.com/en-us/research/articles/sentinelbench-a-benchmark-for-long-running-monitoring-agents/",
            "agentic_security_fabric": "https://www.gartner.com/en/documents/8595537",
            "federated_agent_governance": "https://arxiv.org/abs/2608.26626",
            "wet_lab_scientific_agents": "https://www.nature.com/articles/d41586-026-03270-1",
            "long_horizon_agent_control_architecture": "https://arxiv.org/abs/2609.19519",
            "trajectory_level_long_horizon_evaluation": "https://arxiv.org/abs/2608.13417",
            "trajectory_auditable_agent_deployment": "https://arxiv.org/abs/2608.16411",
        }
        for signal_id, source in required_sources.items():
            with self.subTest(signal=signal_id):
                self.assertIn(signal_id, signals)
                signal = signals[signal_id]
                self.assertIn(source, signal["sources"])
                self.assertTrue(signals[signal_id]["measured_gap"].strip())
                self.assertTrue(signals[signal_id]["benchmark"].strip())
                self.assertTrue(signals[signal_id]["acceptance_threshold"].strip())
                self.assertTrue(signals[signal_id]["rollback"].strip())
        observation = radar["repository_observation"]
        self.assertEqual(observation["main_sha"], "f5739d5f809a76d0d52f2138422ea52124eda5e5")
        self.assertEqual(observation["current_main_check_runs"],
                         {"status": "terminal", "total": 28, "success": 28,
                          "skipped": 0, "failed": 0, "pending": 0})
        self.assertEqual(observation["skipped_checks"], [])
        self.assertEqual(observation["post_merge_push_workflows"]["total"], 6)
        self.assertEqual(observation["post_merge_push_workflows"]["success"], 6)
        self.assertEqual(observation["open_pr_count"], 1)
        self.assertEqual([item["number"] for item in observation["open_prs"]], [1305])
        self.assertIn("after PR #1307 merged", observation["snapshot_note"])
        open_prs = {item["number"]: item for item in observation["open_prs"]}
        self.assertEqual(open_prs[1305]["base_sha"], radar["baseline_main_sha"])
        self.assertEqual(radar["artifact_integrity"]["manifest_file_count"], 538)
        self.assertEqual(radar["artifact_integrity"]["manifest_hash_or_size_mismatches"], 0)
        self.assertEqual(radar["artifact_integrity"]["missing_manifest_entries"], 0)
        self.assertEqual(radar["artifact_integrity"]["workflow_run_id"], 42493782068)
        self.assertEqual(radar["artifact_integrity"]["outer_bundle_artifact_sha256"],
                         "ec729395e71a193e45694a081b56bbb1e0243e02b5b0d97ec32eb2f115ffff98")
        self.assertEqual(radar["artifact_integrity"]["outer_checksum_artifact_sha256"],
                         "0a789bbba81b16df51217e504a01cd6fd290cb21835474dcbf3ecac5dc9a0248")
        self.assertEqual(radar["artifact_integrity"]["inner_bundle_sha256"],
                         "b96de0c00c654b3ceb796d88474a9a87b580de6fe4f8c996df0947ff457341e8")
        self.assertEqual(radar["artifact_integrity"]["source_commit"],
                         "f5739d5f809a76d0d52f2138422ea52124eda5e5")
        self.assertEqual(signals["long_running_host_sandbox"]["deployment_status"], "not verified")
        self.assertEqual(signals["realistic_agent_evaluation"]["independent_evaluation_status"], "blocked")
        self.assertIn("No AGI/SI claim", radar["status"])
        self.assertIn("https://arxiv.org/abs/2611.02153",
                      signals["agent_coordination_action_oversight"]["sources"])
        self.assertIn("collusion", signals["agent_coordination_action_oversight"]["benchmark"].lower())
        self.assertIn("https://www.nature.com/articles/s44386-026-00075-0",
                      signals["wet_lab_scientific_agents"]["sources"])
        for signal_id in (
            "long_horizon_agent_control_architecture",
            "trajectory_level_long_horizon_evaluation",
            "trajectory_auditable_agent_deployment",
        ):
            with self.subTest(signal=signal_id):
                signal = signals[signal_id]
                self.assertEqual(signal["deployment_status"], "not verified")
                self.assertEqual(signal["independent_evaluation_status"], "blocked")
                self.assertTrue(signal["owner"])
                self.assertIn("rollback", signal)
                self.assertIn("independent", signal["acceptance_threshold"].lower())
        self.assertEqual(observation["current_main_check_runs"]["success"], 28)
        self.assertEqual(observation["post_merge_push_workflows"]["success"], 6)
        self.assertEqual(radar["artifact_integrity"]["source_commit"], radar["baseline_main_sha"])



if __name__ == "__main__":
    unittest.main()
