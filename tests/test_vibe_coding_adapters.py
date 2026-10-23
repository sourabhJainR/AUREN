from __future__ import annotations

import unittest
import tempfile
import os
import multiprocessing
import sqlite3
from contextlib import closing
from pathlib import Path

from portable.vibe_coding import STAGE_ORDER, STAGE_SPECS, VibeStage
from portable.vibe_coding_adapters import (
    AdapterRegistration,
    AdapterUnavailable,
    VibeAdapterRegistry,
    build_host_adapter_registry,
    build_integrated_vibe_coding_graph,
    default_stage_capabilities,
)
from portable.orchestration import Orchestrator, RunStatus
from portable.provider_operation_lifecycle import ProviderOutcomeBlocked
from portable.side_effect_ledger import DurableSideEffectLedger
from portable.vibe_coding_host import SideEffectJournal


def _crash_after_vibe_stage_receipt(database: str, call_log: str) -> None:
    ledger = DurableSideEffectLedger(database)
    journal = SideEffectJournal(database)
    def record_event(event):
        if event.get("state") == "completed":
            # Provider receipt is already committed; kill the caller before
            # completion-event projection and before the graph receives its result.
            os._exit(87)
        journal.record_provider_event(event)
    registry = VibeAdapterRegistry(
        max_risk="critical",
        side_effect_ledger=ledger,
        side_effect_event_recorder=record_event,
    )
    stage = VibeStage.PREVIEW_DEPLOYMENT
    def invoke(request):
        with open(call_log, "a", encoding="utf-8") as handle:
            handle.write("dispatched\n")
        return {**output_for(stage, request["state"]), "provider_operation_id": "remote-preview-acceptance-1"}
    registry.register(AdapterRegistration(
        "crash-deployment-adapter", "1", (stage,),
        frozenset(default_stage_capabilities()[stage.value]),
        invoke,
        risk=next(spec.risk for spec in STAGE_SPECS if spec.stage == stage),
        external_side_effects=True,
    ))
    registry.invoke(stage, {
        "vibe_coding_workflow_id": "crash-replay-workflow",
        "intent": "deploy preview",
        "risk": "high",
    })
    os._exit(88)


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
                risk=next(spec.risk for spec in STAGE_SPECS if spec.stage == stage),
                evidence_quality=0.9,
                historical_success=0.9,
            ))
        return registry

    def test_host_factory_wires_lookup_ports_to_side_effect_stages(self):
        capabilities = {
            capability
            for stage_caps in default_stage_capabilities().values()
            for capability in stage_caps
            if capability != "agent"
        }
        tools = {capability: (lambda _request: {}) for capability in capabilities}
        lookup = lambda operation_id, operation_key: {
            "status": "not_found", "operation_id": operation_id, "operation_key": operation_key,
        }
        without_lookup = build_host_adapter_registry(
            lambda _request: {}, tools, max_risk="critical",
        )
        with_lookup = build_host_adapter_registry(
            lambda _request: {}, tools, max_risk="critical",
            lookup_operations={"preview_deployment": lookup},
        )
        adapter = with_lookup._adapters["host-integrated:preview_deployment"]
        self.assertIs(adapter.lookup_operation, lookup)
        self.assertIsNone(with_lookup._adapters["host-integrated:production_deploy"].lookup_operation)
        self.assertNotEqual(without_lookup.configuration_digest, with_lookup.configuration_digest)

        with self.assertRaisesRegex(ValueError, "only configurable for side-effect stages"):
            build_host_adapter_registry(
                lambda _request: {}, tools, max_risk="critical",
                lookup_operations={"testing": lookup},
            )

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
            VibeStage.PROJECT_RULES: {"repository": ("rules",)},
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
            extra = {}
            if stage == VibeStage.PREVIEW_DEPLOYMENT and capability == "deployment":
                extra["environment"] = "preview"
            if stage == VibeStage.PREVIEW_DEPLOYMENT and capability == "preview":
                extra.update({"preview_url": "http://127.0.0.1:8123", "health_status": "passed"})
            if stage == VibeStage.DEVELOPMENT and capability == "repository":
                extra["provider_operation_id"] = "remote-development-42"
            if stage == VibeStage.PREVIEW_DEPLOYMENT and capability == "deployment":
                extra["provider_operation_id"] = "remote-preview-deployment-42"
            if stage == VibeStage.PRODUCTION_DEPLOY and capability == "release":
                extra["environment"] = "production"
                extra["provider_operation_id"] = "remote-production-release-42"
            return {
                **{field: stage_output[field] for field in fields if field in stage_output},
                **extra,
                "evidence": [{"source": capability, "locator": f"tool://{stage.value}/{capability}"}],
            }
        tools = {
            capability: (lambda request, _capability=capability: tool_for(request, _capability))
            for capability in capabilities
        }
        def active_agent(request):
            return output_for(VibeStage(request["stage"]), request["state"])
        with self.assertRaisesRegex(ValueError, "cannot understate"):
            build_host_adapter_registry(
                active_agent, tools,
                risk_by_stage={VibeStage.PRODUCTION_DEPLOY: "high"},
            )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        side_effect_ledger = DurableSideEffectLedger(Path(directory.name) / "side-effects.sqlite3")
        registry = build_host_adapter_registry(
            active_agent, tools, max_risk="critical", side_effect_ledger=side_effect_ledger,
        )
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



    def test_process_death_after_vibe_receipt_commit_replays_without_redispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "operations.sqlite3"
            call_log = Path(directory) / "provider-calls.log"
            process = multiprocessing.get_context("spawn").Process(
                target=_crash_after_vibe_stage_receipt,
                args=(str(database), str(call_log)),
            )
            process.start()
            # Windows spawn can be slow while this large resilience suite runs
            # alongside other jobs. Keep a bounded wait, but avoid mistaking
            # runner contention for a broken crash-recovery fixture.
            process.join(60)
            if process.is_alive():
                process.terminate()
                process.join(5)
                self.fail("Vibe receipt crash fixture exceeded its 60-second time budget")
            self.assertEqual(87, process.exitcode)
            self.assertEqual(["dispatched"], call_log.read_text(encoding="utf-8").splitlines())

            ledger = DurableSideEffectLedger(database)
            journal = SideEffectJournal(database)
            replay_registry = VibeAdapterRegistry(
                max_risk="critical",
                side_effect_ledger=ledger,
                side_effect_event_recorder=journal.record_provider_event,
            )
            stage = VibeStage.PREVIEW_DEPLOYMENT
            def must_not_dispatch(_request):
                self.fail("committed Vibe receipt must replay without another provider call")
            replay_registry.register(AdapterRegistration(
                "crash-deployment-adapter", "1", (stage,),
                frozenset(default_stage_capabilities()[stage.value]),
                must_not_dispatch,
                risk=next(spec.risk for spec in STAGE_SPECS if spec.stage == stage),
                external_side_effects=True,
            ))
            result = replay_registry.invoke(stage, {
                "vibe_coding_workflow_id": "crash-replay-workflow",
                "intent": "deploy preview",
                "risk": "high",
            })
            self.assertEqual("preview-receipt", result["deployment_receipt"])
            self.assertEqual(["dispatched"], call_log.read_text(encoding="utf-8").splitlines())
            self.assertEqual(
                "completed",
                ledger.get("vibe-stage:crash-replay-workflow:preview_deployment:crash-deployment-adapter:1")["state"],
            )
            with closing(sqlite3.connect(database)) as db, db:
                event_states = [
                    row[0] for row in db.execute(
                        "SELECT state FROM provider_operation_events WHERE operation_key=? ORDER BY rowid",
                        ("vibe-stage:crash-replay-workflow:preview_deployment:crash-deployment-adapter:1",),
                    )
                ]
            self.assertEqual(["dispatching", "completed"], event_states)

    def test_external_side_effect_adapter_fails_closed_without_durable_ledger(self):
        registry = VibeAdapterRegistry(max_risk="critical")
        stage = VibeStage.PREVIEW_DEPLOYMENT
        calls = []
        registry.register(AdapterRegistration(
            "deployment-adapter", "1", (stage,),
            frozenset(default_stage_capabilities()[stage.value]),
            lambda request: calls.append(request) or output_for(stage, request["state"]),
            risk=next(spec.risk for spec in STAGE_SPECS if spec.stage == stage),
            external_side_effects=True,
        ))
        with self.assertRaisesRegex(AdapterUnavailable, "no durable side-effect ledger"):
            registry.invoke(stage, {
                "vibe_coding_workflow_id": "workflow-without-ledger",
                "intent": "deploy preview",
            })
        self.assertEqual([], calls)

    def test_uncertain_vibe_side_effect_uses_trusted_lookup_without_redispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "operations.sqlite3"
            operation_key = "vibe-stage:lookup-workflow:preview_deployment:lookup-adapter:1"
            observed = {"operation_id": None}
            evidence_ref = "provider://operations/lookup-fixture"

            def verify(key, evidence, receipt):
                return (
                    key == operation_key
                    and evidence == evidence_ref
                    and observed["operation_id"] is not None
                    and receipt.get("status") == "completed"
                    and receipt.get("provider_operation_id") == observed["operation_id"]
                )

            ledger = DurableSideEffectLedger(database, reconciliation_verifier=verify)
            stage = VibeStage.PREVIEW_DEPLOYMENT
            state = {
                "vibe_coding_workflow_id": "lookup-workflow",
                "intent": "deploy preview",
                "risk": "high",
            }
            first_calls = []
            def uncertain_dispatch(request):
                first_calls.append(request)
                raise RuntimeError("simulated lost acknowledgement after remote acceptance")

            first = VibeAdapterRegistry(max_risk="critical", side_effect_ledger=ledger)
            first.register(AdapterRegistration(
                "lookup-adapter", "1", (stage,),
                frozenset(default_stage_capabilities()[stage.value]),
                uncertain_dispatch,
                risk=next(spec.risk for spec in STAGE_SPECS if spec.stage == stage),
                external_side_effects=True,
            ))
            with self.assertRaises(ProviderOutcomeBlocked):
                first.invoke(stage, state)
            self.assertEqual(1, len(first_calls))
            self.assertEqual("reconcile_required", ledger.get(operation_key)["state"])

            def lookup(operation_id, key):
                self.assertEqual(operation_key, key)
                observed["operation_id"] = operation_id
                return {
                    "status": "completed",
                    "evidence_ref": evidence_ref,
                    "receipt": {
                        "status": "completed",
                        "provider_operation_id": operation_id,
                        "result": output_for(stage, {}),
                    },
                }

            second_calls = []
            second = VibeAdapterRegistry(max_risk="critical", side_effect_ledger=ledger)
            second.register(AdapterRegistration(
                "lookup-adapter", "1", (stage,),
                frozenset(default_stage_capabilities()[stage.value]),
                lambda request: second_calls.append(request) or self.fail(
                    "trusted lookup must reconcile without redispatch"
                ),
                risk=next(spec.risk for spec in STAGE_SPECS if spec.stage == stage),
                external_side_effects=True,
                lookup_operation=lookup,
            ))
            result = second.invoke(stage, state)
            self.assertEqual("preview-receipt", result["deployment_receipt"])
            self.assertEqual([], second_calls)
            self.assertEqual("completed", ledger.get(operation_key)["state"])


    def test_external_side_effect_requires_provider_native_operation_id(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = DurableSideEffectLedger(Path(directory) / "side-effects.sqlite3")
            registry = VibeAdapterRegistry(max_risk="critical", side_effect_ledger=ledger)
            stage = VibeStage.PREVIEW_DEPLOYMENT
            calls = []
            registry.register(AdapterRegistration(
                "deployment-without-remote-id", "1", (stage,),
                frozenset(default_stage_capabilities()[stage.value]),
                lambda request: calls.append(request) or output_for(stage, request["state"]),
                risk=next(spec.risk for spec in STAGE_SPECS if spec.stage == stage),
                external_side_effects=True,
            ))
            state = {
                "vibe_coding_workflow_id": "workflow-missing-provider-id",
                "intent": "deploy preview",
                "risk": "high",
            }
            with self.assertRaises(ProviderOutcomeBlocked):
                registry.invoke(stage, state)
            self.assertEqual(1, len(calls))
            operation_key = (
                "vibe-stage:workflow-missing-provider-id:preview_deployment:"
                "deployment-without-remote-id:1"
            )
            self.assertEqual("reconcile_required", ledger.get(operation_key)["state"])
            with self.assertRaises(ProviderOutcomeBlocked):
                registry.invoke(stage, state)
            self.assertEqual(1, len(calls))

    def test_external_side_effect_adapter_replays_durable_receipt_without_second_call(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = DurableSideEffectLedger(Path(directory) / "side-effects.sqlite3")
            registry = VibeAdapterRegistry(max_risk="critical", side_effect_ledger=ledger)
            stage = VibeStage.PREVIEW_DEPLOYMENT
            calls = []
            def invoke(request):
                calls.append(request)
                return {**output_for(stage, request["state"]), "provider_operation_id": "remote-preview-acceptance-2"}
            registry.register(AdapterRegistration(
                "deployment-adapter", "1", (stage,),
                frozenset(default_stage_capabilities()[stage.value]),
                invoke,
                risk=next(spec.risk for spec in STAGE_SPECS if spec.stage == stage),
                external_side_effects=True,
            ))
            state = {
                "vibe_coding_workflow_id": "workflow-preview-42",
                "intent": "deploy preview",
                "risk": "high",
            }
            first = registry.invoke(stage, state)
            second = registry.invoke(stage, state)
            self.assertEqual(1, len(calls))
            self.assertEqual("preview-receipt", first["deployment_receipt"])
            self.assertEqual(first["deployment_receipt"], second["deployment_receipt"])
            operation_key = "vibe-stage:workflow-preview-42:preview_deployment:deployment-adapter:1"
            provider_operation = calls[0]["provider_operation"]
            self.assertEqual(operation_key, provider_operation["idempotency_key"])
            self.assertEqual("deployment-adapter", provider_operation["provider"])
            self.assertEqual("1", provider_operation["adapter_version"])
            self.assertTrue(provider_operation["operation_id"].startswith("auren-op-"))
            self.assertEqual("completed", ledger.get(operation_key)["state"])

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
            lambda _request: {}, risk="medium", historical_success=0.95, evidence_quality=0.95,
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
            invoke, risk="medium",
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


    def test_learning_store_autosaves_invocations_and_evaluations_across_registry_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "private" / "learning.json"
            first = self.populated_registry()
            first.attach_learning_store(target)
            first.invoke(
                VibeStage.IDEA,
                {"risk": "low", "vibe_coding_workflow_id": "session-a", "intent": "Build a product"},
                capabilities=default_stage_capabilities()[VibeStage.IDEA.value],
            )
            first.record_evaluation(
                "adapter-idea", workflow_id="evaluation-a",
                passed=True, evidence_refs=["evidence://independent/one"],
            )
            self.assertTrue(target.is_file())
            if os.name != "nt":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            second = self.populated_registry()
            second.attach_learning_store(target)
            snapshot = second.snapshot()
            self.assertEqual(snapshot["stats"]["adapter-idea"]["successes"], 1)
            self.assertEqual(snapshot["evaluations"]["adapter-idea"]["successes"], 1)
            self.assertIn("evidence://independent/one", snapshot["evaluations"]["adapter-idea"]["evidence_refs"])

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
