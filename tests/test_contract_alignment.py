from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATHS = (
    ROOT / "skills" / "ai-coding-orchestrator" / "SKILL.md",
    ROOT / ".agents" / "skills" / "ai-coding-orchestrator" / "SKILL.md",
    ROOT / ".claude" / "skills" / "ai-coding-orchestrator" / "SKILL.md",
)
RUNTIME_PATHS = (
    ROOT / ".ai-harness" / "runtime" / "tool_runner.py",
    ROOT / ".ai-harness" / "runtime" / "lsp_server.py",
    ROOT / ".ai-harness" / "runtime" / "feedback_loop.py",
    ROOT / ".ai-harness" / "runtime" / "auto_compaction.py",
)
COMPOSED_SKILLS = (
    ROOT / "skills" / "engineering" / "retro" / "SKILL.md",
    ROOT / "skills" / "engineering" / "research" / "SKILL.md",
    ROOT / "skills" / "engineering" / "prototype" / "SKILL.md",
    ROOT / "skills" / "engineering" / "resolving-merge-conflicts" / "SKILL.md",
    ROOT / "skills" / "engineering" / "interactive-documentation" / "SKILL.md",
)


class ContractAlignmentTests(unittest.TestCase):
    def test_all_skill_entrypoints_are_identical(self) -> None:
        contents = [path.read_text(encoding="utf-8") for path in SKILL_PATHS]
        self.assertTrue(all(contents), "canonical skill entrypoints must exist")
        self.assertEqual(len(set(contents)), 1, "skill entrypoints have diverged")
        canonical = contents[0]
        for token in (
            "portable.task_planner.TaskPlan",
            "portable.impact_analysis",
            "portable.repo_intelligence.RepositoryMap",
            "CodebaseIndex",
            "ContextEvidence",
            "skills are orchestration surfaces",
            ".ai-harness/runtime/tool_runner.py",
            ".ai-harness/runtime/lsp_server.py",
            ".ai-harness/runtime/feedback_loop.py",
            ".ai-harness/runtime/auto_compaction.py",
            "interactive-documentation",
            "downgrade=explicit_install_only",
        ):
            self.assertIn(token, canonical)

    def test_grilling_and_rework_are_core_contracts(self) -> None:
        policy = ROOT / "skills" / "engineering" / "grilling-and-rework.md"
        self.assertTrue(policy.is_file(), "core grilling/rework policy must be present")
        text = policy.read_text(encoding="utf-8")
        for token in (
            "dependency-aware rounds",
            "user retains authority",
            "rework the implementation, not the verdict",
            "at most two attempts",
            "exact current head",
            "Unresolved decisions block only tasks that depend on them",
        ):
            self.assertIn(token, text)
        canonical = (ROOT / "skills" / "ai-coding-orchestrator" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("skills/engineering/grilling-and-rework.md", canonical)
        self.assertIn("Core grilling and rework foundation", canonical)
        self.assertIn("Actionable output contract", text)
        self.assertIn("expected result or acceptance condition", text)
        self.assertIn("Make blockers actionable", text)
        self.assertIn("Actionable output is part of completion", canonical)

    def test_active_docs_use_current_identity_and_version_authority(self) -> None:
        active_identity_docs = (
            "docs/INDEPENDENT_GENERALIZATION_ARENA.md",
            "docs/MATT_POCOCK_SKILLS_INTEGRATION.md",
            ".ai-harness/ORCHESTRATION_SPEC.md",
            ".ai-harness/CORE_RUNTIME_SERVICES.md",
            "portable/LANGGRAPH_PATTERN_ALIGNMENT.md",
            "docs/CANONICAL_AGENT_PATTERN_COMPOSITION.md",
            "docs/END_USER_CONSOLE.md",
            "docs/ENGINEERING_CONSOLE.md",
            "docs/OPENHANDS_TASKMASTER_ADAPTATION.md",
            "agency/README.md",
            "agency/UPSTREAM_NOTICE.md",
            "agency/AGENCY_AGENT_QUALITY.md",
            "dashboard/README.md",
            "docs/SECOND_BRAIN.md",
            "docs/CANARY_DESIGN.md",
            "docs/LOCAL_OFFLOAD.md",
            "docs/RUST_MIGRATION.md",
            "docs/LEARNING_ENGINE.md",
            "docs/REGRESSION_CANARY.md",
            "docs/LOCAL_LLM_FALLBACK.md",
            "docs/ENGINEERING_EPISODE.md",
            "docs/RIPWIRE_INTEGRATION.md",
            ".ai-harness/HERMES_PARITY.md",
            ".ai-harness/PROVIDER_CONTRACT.md",
            ".ai-harness/SECOND_BRAIN_SPEC.md",
            ".ai-harness/CONTEXT_POLICY.md",
            ".ai-harness/GENAI_CODING_POLICY.md",
            "docs/SKILLOPT_INTEGRATION.md",
            "docs/GENAI_CODING_ADAPTATION.md",
            "docs/LOCAL_MISSION_WORKBENCH.md",
            "docs/ROWBOAT_ADAPTATION_SPEC.md",
            "skills/engineering/retro/SKILL.md",
        )
        for relative in active_identity_docs:
            content = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertNotRegex(content, r"\bAER\b", f"stale product identity in {relative}")
        arena = (ROOT / "docs" / "INDEPENDENT_GENERALIZATION_ARENA.md").read_text(encoding="utf-8")
        integration = (ROOT / "docs" / "MATT_POCOCK_SKILLS_INTEGRATION.md").read_text(encoding="utf-8")
        self.assertIn("issue #368", arena)
        self.assertIn("`VERSION` is the canonical release version", integration)
        self.assertNotIn("21.0.0", integration)
        self.assertNotIn("22.0.0", integration)

    def test_graph_runtime_docs_match_the_real_module_boundaries(self) -> None:
        supervisor_graph = (ROOT / "portable" / "state_graph.py").read_text(encoding="utf-8")
        lower_level_graph = (ROOT / "portable" / "agency_state_graph.py").read_text(encoding="utf-8")
        controller_docs = (ROOT / "docs" / "DEFAULT_EXECUTION_CONTROLLER.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("class StateGraph:", supervisor_graph)
        self.assertIn("class GraphAgentTeam:", supervisor_graph)
        self.assertIn("class StateGraph:", lower_level_graph)
        for token in ("portable.state_graph.StateGraph", "portable.state_graph.GraphAgentTeam",
                      "portable.agency_state_graph.StateGraph", "#379", "#377"):
            self.assertIn(token, controller_docs)
        self.assertIn("Standalone checkpointable graph runtime", readme)
        self.assertIn("Default graph orchestration", readme)

    def test_unified_graph_crew_contract_matches_current_runtime_boundaries(self) -> None:
        contract = (ROOT / "docs" / "UNIFIED_GRAPH_CREW_RUNTIME.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        compatibility = json.loads((ROOT / "architecture" / "compatibility.yaml").read_text(encoding="utf-8"))
        for token in (
            "portable.orchestration.Graph/Orchestrator",
            "portable.agency_state_graph.StateGraph",
            "portable.state_graph.GraphAgentTeam",
            "portable.crew_adapters",
            "issue #438",
            "host registers adapters at bootstrap",
            "Guard authorization",
        ):
            self.assertIn(token, contract)
        self.assertIn("issue #438", readme)
        runtime_entry = (ROOT / ".ai-harness" / "runtime" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('get_auren_env("GRAPH_TEAM", "1")', runtime_entry)
        self.assertIn('from auren_env_compat import get_auren_env', runtime_entry)
        resolver_entry = (ROOT / "auren_env_compat.py").read_text(encoding="utf-8")
        self.assertIn('key.replace("AUREN_", "AER_", 1)', resolver_entry)
        team_strategy = next(
            item for item in compatibility["surfaces"]
            if item["name"] == ".ai-harness/runtime/graph_agent_team.py"
        )
        self.assertEqual(team_strategy["status"], "strategy")
        self.assertEqual(team_strategy["canonical_owner"], "portable.agency_state_graph.StateGraph")
        self.assertIn("canonical crew-contract adapter", team_strategy["replacement"])

    def test_runtime_service_paths_are_present(self) -> None:
        missing = [str(path.relative_to(ROOT)) for path in RUNTIME_PATHS if not path.is_file()]
        self.assertEqual(missing, [])

    def test_composed_skills_are_present_and_thin(self) -> None:
        missing = [str(path.relative_to(ROOT)) for path in COMPOSED_SKILLS if not path.is_file()]
        self.assertEqual(missing, [])
        for path in COMPOSED_SKILLS:
            content = path.read_text(encoding="utf-8")
            self.assertIn("canonical", content.lower(), str(path))
            self.assertTrue(any(token in content for token in ("evidence", "verification", "provenance")), str(path))

    def test_interactive_renderer_is_standalone(self) -> None:
        renderer = ROOT / "skills" / "engineering" / "interactive-documentation" / "render_document.py"
        self.assertTrue(renderer.is_file())
        sample = {
            "title": "Test map",
            "repository": "test",
            "snapshot_digest": "abc",
            "generated_at": "2026-09-16T00:00:00Z",
            "nodes": [
                {"id": "a", "label": "A", "kind": "service", "sources": ["a.py::A"], "evidence_ids": ["ev-1"]},
                {"id": "b", "label": "B", "kind": "store", "sources": ["b.py::B"], "evidence_ids": ["ev-2"]},
            ],
            "edges": [{"source": "a", "target": "b", "label": "writes"}],
            "views": [{"id": "overview", "label": "Overview", "nodes": ["a", "b"]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "map.json"
            output = Path(tmp) / "map.html"
            source.write_text(json.dumps(sample), encoding="utf-8")
            subprocess.run([sys.executable, str(renderer), str(source), str(output)], check=True)
            html = output.read_text(encoding="utf-8")
        self.assertIn("Interactive system map", html)
        self.assertIn("Snapshot", html)
        self.assertNotIn("cdn.", html.lower())
        self.assertNotIn("https://", html.lower())

    def test_plugin_versions_are_aligned(self) -> None:
        canonical = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(canonical, "1.0.4")
        self.assertEqual(plugin["version"], canonical)
        self.assertEqual(marketplace["version"], canonical)
        self.assertEqual(marketplace["plugins"][0]["version"], canonical)

    def test_artifact_contract_matches_explicit_install_semantics(self) -> None:
        contract = json.loads((ROOT / ".ai-harness" / "ARTIFACT_UPGRADE_CONTRACT.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["downgrade"], "explicit_install_only")
        self.assertEqual(contract["automatic_update_downgrade"], "forbidden")


    def test_engineering_principles_cover_the_canonical_families_and_runtime_lenses(self) -> None:
        catalogue = (ROOT / "ENGINEERING_PRINCIPLES.md").read_text(encoding="utf-8")
        policy = (ROOT / ".ai-harness" / "ENGINEERING_DESIGN_POLICY.md").read_text(encoding="utf-8")
        guard = (ROOT / "portable" / "engineering_design_guard.py").read_text(encoding="utf-8")
        for token in (
            "Canonical 12-family taxonomy",
            "Fundamental design",
            "Security and privacy",
            "Performance and cost",
            "Concurrency",
            "Distributed systems",
            "Reliability and recovery",
            "Data and correctness",
            "API and integration",
            "Testing and verification",
            "Delivery and operations",
            "Observability and governance",
            "AI and autonomous systems",
            "CAP",
            "PACELC",
            "split brain",
            "idempotency",
            "backoff/jitter",
            "liveness",
            "readiness",
            "preconditions/postconditions",
            "optimistic concurrency",
            "schema evolution",
            "provenance",
            "independent holdout",
            "rollback",
            "Required task lifecycle",
            "exact final head",
        ):
            self.assertIn(token.lower(), catalogue.lower(), f"principle catalogue missing: {token}")
        self.assertIn("Canonical 12-family task selection", policy)
        for token in ("DesignDimension.DISTRIBUTED", "DesignDimension.RESILIENCE", "DesignDimension.CORRECTNESS", "DesignDimension.AI_AGENT"):
            self.assertIn(token, guard)

    def test_task_retry_budget_is_shared_by_harness_and_crew_handoffs(self) -> None:
        runtime = (ROOT / ".ai-harness" / "runtime" / "graph_agent_team.py").read_text(encoding="utf-8")
        crew_contract = (ROOT / "portable" / "crew_adapters.py").read_text(encoding="utf-8")
        graph_contract = (ROOT / "portable" / "state_graph.py").read_text(encoding="utf-8")
        self.assertIn("TaskRetryBudget", runtime)
        self.assertIn("merge_retry_budget_snapshots", runtime)
        self.assertIn('reducers={"auren_retry_budget": merge_retry_budget_snapshots}', runtime)
        self.assertIn("retry_budget.consume(", runtime)
        self.assertIn("TaskRetryBudget.resume(saved_budget)", runtime)
        self.assertIn("component=\"crew_handoff\"", crew_contract)
        self.assertIn("require_durable_handoffs", graph_contract)
        self.assertIn("auren_crew_handoff_checkpoint_data", graph_contract)

    def test_identity_allowlist_is_limited_to_docs_tests_and_named_compatibility_files(self) -> None:
        from scripts.validate_auren_identity import LEGACY_ALLOWED, _is_approved_allowlist_path

        self.assertTrue(all(_is_approved_allowlist_path(path) for path in LEGACY_ALLOWED))
        self.assertNotIn("portable/state_graph.py", LEGACY_ALLOWED)
        self.assertNotIn(".ai-harness/runtime/graph_agent_team.py", LEGACY_ALLOWED)
        self.assertNotIn("portable/orchestration.py", LEGACY_ALLOWED)

    def test_identity_audit_detects_legacy_env_names_and_state_roots(self) -> None:
        from scripts.validate_auren_identity import PATTERNS

        for sample in ("AER_HOME", "AER_LOCAL_LLM_ENABLED", "~/.aer/memory.db", "aer_execution_mode"):
            with self.subTest(sample=sample):
                self.assertTrue(any(pattern.search(sample) for pattern in PATTERNS), sample)

    def test_active_identity_audit_uses_narrow_runtime_compatibility_exceptions(self) -> None:
        audit = (ROOT / "scripts" / "validate_auren_identity.py").read_text(encoding="utf-8")
        self.assertNotIn('"portable/state_graph.py"', audit)
        self.assertNotIn('"portable/session_state.py"', audit)
        self.assertNotIn('".ai-harness/runtime/legacy_compatibility.py"', audit)
        self.assertIn('".ai-harness/runtime/graph_agent_team.py": (', audit)
        self.assertIn('state\\.get\\("auren_execution_strategy"', audit)
        self.assertIn('state\\.get\\("auren_execution_mode"', audit)

    def test_active_runtime_and_user_facing_docs_use_canonical_auren_identity(self) -> None:
        import re

        active_paths = (
            "dashboard/index.html",
            "portable/dashboard_server.py",
            "portable/user_experience.py",
            "portable/provider_fabric.py",
            "portable/local_llm.py",
            "portable/output_quality.py",
            "portable/lifecycle_hooks.py",
            "portable/chat_capability.py",
            "portable/persistent_memory.py",
            "portable/capability_fabric.py",
            "portable/cognitive_runtime.py",
            "portable/repo_intelligence.py",
            "portable/engineering_design_guard.py",
            "portable/agency_runtime.py",
            "portable/local_offload.py",
            ".ai-harness/safe_provider.py",
            "scripts/provider_conformance.py",
            "scripts/sync_agency_agents.py",
            ".ai-harness/PROVIDER_CONFORMANCE.md",
            ".ai-harness/BEHAVIORAL_CONFORMANCE.md",
            ".ai-harness/SELF_IMPROVEMENT_POLICY.md",
            ".ai-harness/HERMES_CAPABILITY_CONTRACT.md",
            ".ai-harness/ENGINEERING_DESIGN_POLICY.md",
            "docs/AI_AGENT_BOOK_V2_ALIGNMENT.md",
            "docs/CAPABILITY_AWARE_EXECUTION.md",
            "docs/LLM_CHAT_TRIGGER.md",
            "docs/CAPABILITY_MEMORY_OWNERSHIP.md",
            "docs/SELF_IMPROVING_CONTROL_PLANE.md",
            "docs/AI_CODING_RESEARCH_ADAPTATION.md",
            "docs/RECURSIVE_AGENCY_AND_SYSTEM_ONE.md",
            "docs/LEARNING_ENGINE_V2.md",
            "docs/AWESOME_LLM_APPS_PATTERN_ADAPTATION.md",
            "docs/JEV_TYPED_DECISION_ALIGNMENT.md",
            "skills/engineering/research/SKILL.md",
            "skills/engineering/prototype/SKILL.md",
            "PROBLEM_SOLVING_FRAMEWORKS.md",
            ".ai-harness/resilient_provider.py",
            "scripts/behavioral_conformance.py",
            "portable/curiosity.py",
            "portable/hermes_runtime.py",
            "portable/agi_evaluation.py",
            ".agents/skills/agency-specialist-orchestrator/SKILL.md",
            "docs/TWELVE_PHASE_ENGINEERING_EVOLUTION.md",
            "docs/CANONICAL_ENGINEERING_EVIDENCE_ENVELOPE.md",
            ".ai-harness/QUALITY_GOVERNANCE.md",
            "docs/EPISODE_SKILL_EVOLUTION.md",
            ".ai-harness/runtime/tool_runner.py",
            "portable/agency_regression_loop.py",
            "portable/causal_experiment.py",
            ".ai-harness/runtime/feedback_loop.py",
            "portable/goal_directed_planning.py",
            "portable/information_planner.py",
            ".ai-harness/runtime/regression_replay.py",
            "portable/causal_model.py",
            "skills/engineering/interactive-documentation/SKILL.md",
            "skills/engineering/resolving-merge-conflicts/SKILL.md",
            "skills/ai-coding-orchestrator/references/EXTENSIONS.md",
        )
        stale_brand = re.compile(r"\bAER\b")
        for relative in active_paths:
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIsNone(stale_brand.search(content), f"stale AER product name in active file: {relative}")
        compatibility = (ROOT / "docs" / "LEGACY_NAME_COMPATIBILITY.md").read_text(encoding="utf-8")
        self.assertIn("AER_GRAPH_TEAM", compatibility)
        self.assertIn("Historical lineage", compatibility)
        self.assertIn("Adversarial test fixture", compatibility)


if __name__ == "__main__":
    unittest.main()
