from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from portable.vibe_coding import STAGE_ORDER, VibeStage
from portable.vibe_coding_adapters import (
    AdapterRegistration,
    AdapterUnavailable,
    VibeAdapterRegistry,
    build_host_adapter_registry,
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


    def test_host_factory_composes_active_agent_and_authoritative_tool_receipts(self):
        authority = {
            VibeStage.RESEARCH: {"research": ("market_evidence", "competitors")},
            VibeStage.PRD: {"product": ("requirements", "acceptance_criteria")},
            VibeStage.DESIGN: {"design": ("design_artifacts",)},
            VibeStage.TASK_BREAKDOWN: {"task_planning": ("tasks",)},
            VibeStage.SETUP: {"repository": ("repository", "environment")},
            VibeStage.DEVELOPMENT: {"repository": ("artifacts",)},
            VibeStage.TESTING: {"test_execution": ("tests_run", "status")},
            VibeStage.SECURITY_REVIEW: {"security": ("findings", "status", "critical_findings")},
            VibeStage.CODE_REVIEW: {"independent_review": ("reviewer", "review_summary", "status", "blocking_findings")},
            VibeStage.PREVIEW_DEPLOYMENT: {"deployment": ("deployment_receipt",)},
            VibeStage.QA_TESTING: {"test_execution": ("qa_results", "user_feedback", "status")},
            VibeStage.PRODUCTION_DEPLOY: {"release": ("release_receipt", "deployed_revision", "reviewed_revision", "approval")},
            VibeStage.MONITORING: {"observability": ("signals", "alerts", "rollback_thresholds")},
        }
        capabilities = {
            cap for values in default_stage_capabilities().values() for cap in values if cap != "agent"
        }
        def tool_for(request, capability):
            stage = VibeStage(request["stage"])
            stage_output = output_for(stage, request["state"])
            fields = authority.get(stage, {}).get(capability, ())
            return {
                **{field: stage_output[field] for field in fields if field in stage_output},
                "evidence": [{"source": capability, "locator": f"tool://{stage.value}/{capability}"}],
            }
        tools = {
            capability: (lambda request, _capability=capability: tool_for(request, _capability))
            for capability in capabilities
        }
        def active_agent(request):
            return output_for(VibeStage(request["stage"]), request["state"])
        registry = build_host_adapter_registry(active_agent, tools, max_risk="critical")
        graph = build_integrated_vibe_coding_graph(registry)
        context = {
            "risk": "low",
            "vibe_coding_workflow_id": "host-factory-run",
            "production_approval": {
                "approved": True, "approved_by": "release-manager",
                "approved_at": "2026-10-15T12:00:00Z",
            },
            "final_code_revision": "revision-123",
            "verified_code_revision": "revision-123",
        }
        run = Orchestrator(graph, max_total_attempts=32).run(
            "host-factory-run", "Build a product", context
        )
        self.assertEqual(run.status, RunStatus.ACCEPTED, run.stop_reason)
        self.assertEqual(len(run.results), 18)

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


    def test_adapter_context_redacts_secrets_and_withholds_release_authority(self):
        registry = VibeAdapterRegistry()
        stage = VibeStage.RESEARCH
        seen = {}
        def invoke(request):
            seen.update(request)
            return output_for(stage, request["state"])
        registry.register(AdapterRegistration(
            "research-safe", "1", (stage,),
            frozenset(default_stage_capabilities()[stage.value]),
            invoke,
        ))
        registry.invoke(stage, {
            "intent": "Research product",
            "api_token": "do-not-forward",
            "production_approval": {"approved": True, "approved_by": "operator"},
            "idea": {"password": "do-not-forward", "summary": "safe"},
        })
        self.assertNotIn("api_token", seen["state"])
        self.assertNotIn("production_approval", seen["state"])
        self.assertNotIn("password", seen["prior_stage_outputs"].get("idea", {}))
        self.assertEqual(seen["prior_stage_outputs"]["idea"]["summary"], "safe")


    def test_learning_snapshot_persists_and_restores_evidence_provenance(self):
        registry = self.populated_registry()
        registry.record_evaluation(
            "adapter-research", workflow_id="workflow-a",
            passed=True, evidence_refs=["evidence://research/one"],
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "machine-state" / "learning.json"
            registry.save_snapshot(target)
            restored = self.populated_registry()
            restored.load_snapshot(target)
        snapshot = restored.snapshot()
        self.assertIn("evidence://research/one", snapshot["evaluations"]["adapter-research"]["evidence_refs"])
        self.assertEqual(snapshot["evaluations"]["adapter-research"]["successes"], 1)

    def test_learning_snapshot_detects_tampering(self):
        registry = self.populated_registry()
        snapshot = registry.snapshot()
        snapshot["stats"]["adapter-idea"]["successes"] = 100
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            registry.restore_stats(snapshot)


if __name__ == "__main__":
    unittest.main()
