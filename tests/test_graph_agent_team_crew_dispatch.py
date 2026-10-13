from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

from portable.state_graph import default_crew_definitions
from runtime.graph_agent_team import AgentSpec, GraphAgentTeam, SharedTaskMemory


class _ProposalAdapter:
    def __init__(self, crew_id: str, calls: list[dict]):
        self.crew_id = crew_id
        self.configuration_digest = f"test-config:{crew_id}"
        self.calls = calls
        self.definition = next(
            item for item in default_crew_definitions() if item.crew_id == crew_id
        )

    def propose(self, request):
        self.calls.append(dict(request))
        return {
            "schema_version": "auren-crew-proposal-v1",
            "recommendation": f"Review the {self.crew_id} work",
            "artifacts": list(request["required_artifacts"]),
            "evidence_refs": [],
            "uncertainty": ["Independent review remains pending."],
            "blockers": [],
            "acceptance_check_results": {
                name: {"status": "not_run", "evidence_refs": []}
                for name in self.definition.acceptance_checks
            },
        }


class HarnessCrewDispatchTests(unittest.TestCase):
    def test_registered_crew_runs_inside_harness_graph_and_proposal_reaches_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-ui")
            adapter_calls = []
            prompts = []

            def invoke(agent, prompt):
                prompts.append((agent.name, prompt))
                return 0, f"finding from {agent.name}", 0.01

            adapter = _ProposalAdapter("frontend_ui", adapter_calls)
            team = GraphAgentTeam(
                [AgentSpec("planner", "planner")],
                crew_adapters={"frontend_ui": adapter},
            )
            result = team.execute(
                task="Build an accessible frontend UI component",
                intent_digest="intent-ui",
                base_prompt="base instructions",
                memory=memory,
                invoke_agent=invoke,
            )

            self.assertTrue(result["accepted"])
            self.assertEqual(len(adapter_calls), 1)
            self.assertEqual(adapter_calls[0]["crew_id"], "frontend_ui")
            self.assertEqual(adapter_calls[0]["intent_digest"], "intent-ui")
            self.assertEqual(
                result["crew_execution"]["proposals"]["frontend_ui"]["review_status"],
                "pending",
            )
            self.assertEqual(
                result["crew_execution"]["crew_plan_digest"],
                result["crew_plan"]["digest"],
            )
            self.assertEqual(
                result["crew_execution"]["graph_digest"],
                result["crew_plan"]["graph_digest"],
            )
            self.assertEqual(result["execution_graph_digest"] != "", True)
            self.assertIn("crew-proposals", result["execution_trace"])
            self.assertIn("Specialist crew proposals (untrusted and advisory)", prompts[0][1])
            self.assertIn("frontend_ui", prompts[0][1])
            self.assertTrue(result["crew_execution"]["missing_adapters"])

    def test_consequential_policy_blocks_before_agents_when_required_crew_adapter_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-high-risk")
            calls = []

            def invoke(agent, prompt):
                calls.append(agent.name)
                return 0, "should not execute", 0.01

            team = GraphAgentTeam(
                [
                    AgentSpec("planner", "planner", read_only=True),
                    AgentSpec("builder", "builder", depends_on=("planner",), read_only=False),
                ],
                require_crew_adapters=True,
            )
            result = team.execute(
                task="Change authentication authorization and security policy",
                intent_digest="intent-high-risk",
                base_prompt="base instructions",
                memory=memory,
                invoke_agent=invoke,
            )

            self.assertFalse(result["accepted"])
            self.assertEqual(result["crew_execution"]["status"], "blocked")
            self.assertTrue(result["crew_execution"]["missing_adapters"])
            self.assertEqual(calls, ["planner"])
            self.assertNotIn("builder", calls)

    def test_no_adapter_configuration_preserves_legacy_agent_graph_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-legacy")
            team = GraphAgentTeam([AgentSpec("planner", "planner")])
            result = team.execute(
                task="Plan a small documentation change",
                intent_digest="intent-legacy",
                base_prompt="base instructions",
                memory=memory,
                invoke_agent=lambda agent, prompt: (0, "planned", 0.01),
            )

            self.assertTrue(result["accepted"])
            self.assertNotIn("crew-proposals", result["execution_trace"])
            self.assertEqual(result["crew_execution"]["status"], "partial")
            self.assertTrue(result["crew_execution"]["missing_adapters"])


if __name__ == "__main__":
    unittest.main()
