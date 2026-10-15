from __future__ import annotations

import unittest

from portable.vibe_coding import STAGE_ORDER, VibeStage
from portable.vibe_coding_adapters import (
    AdapterRegistration,
    AdapterUnavailable,
    VibeAdapterRegistry,
    build_integrated_vibe_coding_graph,
    default_stage_capabilities,
)
from portable.orchestration import Orchestrator, RunStatus


def output_for(stage, state):
    values = {
        VibeStage.IDEA: {"problem": "Need a clear product workflow", "success_metrics": ["acceptance passed"]},
        VibeStage.RESEARCH: {"market_evidence": [{"source": "research-adapter", "locator": "evidence://market"}], "competitors": ["alternative-a"]},
        VibeStage.DEFINE_USER: {"personas": ["primary user"], "user_needs": ["complete workflow"]},
        VibeStage.PRD: {"requirements": ["core requirement"], "acceptance_criteria": ["test passes"]},
        VibeStage.TECH_STACK: {"technology_decisions": ["Python fits the existing runtime"]},
        VibeStage.ARCHITECTURE: {"architecture_decisions": ["explicit interfaces"]},
        VibeStage.DESIGN: {"design_artifacts": ["wireframe-v1"]},
        VibeStage.PROJECT_RULES: {"rules": ["test", "review", "least privilege"]},
        VibeStage.TASK_BREAKDOWN: {"tasks": ["implement", "verify"]},
        VibeStage.SETUP: {"repository": "workspace", "environment": {"python": "3.12"}},
        VibeStage.DEVELOPMENT: {"artifacts": ["src/feature.py"]},
        VibeStage.TESTING: {"tests_run": ["unit suite"], "status": "passed"},
        VibeStage.SECURITY_REVIEW: {"findings": [], "critical_findings": 0, "status": "passed"},
        VibeStage.CODE_REVIEW: {"reviewer": "independent-reviewer", "author": "implementer", "review_summary": "No blocking issues", "blocking_findings": 0, "status": "passed"},
        VibeStage.PREVIEW_DEPLOYMENT: {"deployment_receipt": "preview-receipt"},
        VibeStage.QA_TESTING: {"qa_results": ["acceptance scenarios passed"], "user_feedback": {"status": "captured"}, "status": "passed"},
        VibeStage.PRODUCTION_DEPLOY: {
            "release_receipt": "release-receipt",
            "approval": dict(state.get("production_approval", {})),
            "reviewed_revision": state.get("verified_code_revision", ""),
            "deployed_revision": state.get("verified_code_revision", ""),
        },
        VibeStage.MONITORING: {"signals": ["error rate"], "alerts": ["error-rate"], "rollback_thresholds": ["SLO breach"]},
    }
    return {"status": "completed", "evidence": [{"source": "test-adapter", "locator": f"test://{stage.value}"}], **values[stage]}


class VibeCodingAdapterTests(unittest.TestCase):
    def populated_registry(self):
        registry = VibeAdapterRegistry(max_risk="critical")
        for stage in STAGE_ORDER:
            required = tuple(default_stage_capabilities()[stage.value])
            def invoke(request, _stage=stage):
                return output_for(_stage, request["state"])
            registry.register(AdapterRegistration(
                adapter_id=f"adapter-{stage.value}",
                version="1",
                stages=(stage,),
                capabilities=frozenset(required),
                invoke=invoke,
                risk="low",
                evidence_quality=0.9,
                historical_success=0.9,
            ))
        return registry

    def test_integrated_graph_binds_all_18_real_adapters(self):
        registry = self.populated_registry()
        graph = build_integrated_vibe_coding_graph(registry)
        self.assertEqual(len(graph.order()), 18)
        context = {
            "risk": "low",
            "vibe_coding_workflow_id": "integration-run",
            "production_approval": {
                "approved": True, "approved_by": "release-manager",
                "approved_at": "2026-10-15T12:00:00Z",
            },
            "final_code_revision": "revision-123",
            "verified_code_revision": "revision-123",
        }
        run = Orchestrator(graph, max_total_attempts=32).run(
            "integration-run", "Build an accessible product", context
        )
        self.assertEqual(run.status, RunStatus.ACCEPTED, run.stop_reason)
        self.assertEqual(list(run.results), [stage.value for stage in STAGE_ORDER])
        for stage in STAGE_ORDER:
            self.assertEqual(registry._stats[f"adapter-{stage.value}"].successes, 1)

    def test_missing_capability_adapter_fails_closed_before_graph_creation(self):
        registry = self.populated_registry()
        registry._adapters.pop("adapter-research")
        with self.assertRaisesRegex(AdapterUnavailable, "research"):
            build_integrated_vibe_coding_graph(registry)

    def test_risk_policy_filters_unsafe_adapter(self):
        registry = VibeAdapterRegistry(max_risk="medium")
        registry.register(AdapterRegistration(
            "high-risk", "1", (VibeStage.RESEARCH,),
            frozenset(default_stage_capabilities()["research"]),
            lambda _request: {}, risk="high",
        ))
        with self.assertRaises(AdapterUnavailable):
            registry.select(VibeStage.RESEARCH)

    def test_self_evolution_only_proposes_with_multiple_workflows_and_evidence(self):
        registry = VibeAdapterRegistry()
        stage = VibeStage.RESEARCH
        registry.register(AdapterRegistration(
            "research", "1", (stage,),
            frozenset(default_stage_capabilities()[stage.value]),
            lambda _request: {}, historical_success=0.95, evidence_quality=0.95,
        ))
        for index in range(6):
            registry.record_evaluation(
                "research", workflow_id=f"workflow-{index % 3}",
                passed=True, evidence_refs=[f"evidence://{index}"],
            )
        candidates = registry.evolution_candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["status"], "candidate_only")
        self.assertIn("holdout_transfer", candidates[0]["required_next_gates"])

    def test_learning_snapshot_detects_tampering(self):
        registry = self.populated_registry()
        snapshot = registry.snapshot()
        snapshot["stats"]["adapter-idea"]["successes"] = 100
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            registry.restore_stats(snapshot)


if __name__ == "__main__":
    unittest.main()
