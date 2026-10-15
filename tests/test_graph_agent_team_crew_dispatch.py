from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

import runtime
from portable.state_graph import default_crew_definitions
from portable.agency_state_graph import InMemoryCheckpointStore
from runtime import register_crew_adapters
from runtime.graph_agent_team import AgentSpec, GraphAgentTeam, SharedTaskMemory


class _ProposalAdapter:
    def __init__(self, crew_id: str, calls: list[dict]):
        self.crew_id = crew_id
        self.configuration_digest = f"test-config:{crew_id}"
        self.implementation_version = "test-v1"
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


class _HandoffProposalAdapter(_ProposalAdapter):
    def __init__(self, crew_id: str, calls: list[dict], *, target_crew: str):
        super().__init__(crew_id, calls)
        self.target_crew = target_crew

    def propose(self, request):
        proposal = super().propose(request)
        proposal["handoff_requests"] = [{
            "target_crew": self.target_crew,
            "task": "Independently review the accessible UI proposal",
        }]
        return proposal


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
                code_revision="exact-source-revision-123",
            )

            self.assertTrue(result["accepted"])
            envelope = result["crew_execution"]["evidence_envelope"]
            self.assertEqual(envelope["source_revision"], "exact-source-revision-123")
            self.assertEqual(len(envelope["digest"]), 64)
            self.assertEqual(len(adapter_calls), 1)
            self.assertEqual(adapter_calls[0]["crew_id"], "frontend_ui")
            self.assertEqual(adapter_calls[0]["evidence"]["intent_digest"], "intent-ui")
            self.assertEqual(
                result["crew_execution"]["proposals"]["frontend_ui"]["review_status"],
                "pending",
            )
            proposal_record = result["crew_execution"]["proposals"]["frontend_ui"]
            self.assertEqual(proposal_record["implementation_version"], "test-v1")
            self.assertEqual(proposal_record["intent_digest"], "intent-ui")
            self.assertEqual(proposal_record["crew_plan_digest"], result["crew_plan"]["digest"])
            self.assertEqual(len(proposal_record["proposal_digest"]), 64)
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

    def test_handoff_request_is_checkpointed_and_dispatched_pending_independent_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-handoff")
            checkpoint = InMemoryCheckpointStore()
            frontend_calls, qa_calls = [], []
            team = GraphAgentTeam(
                [AgentSpec("planner", "planner")],
                crew_adapters={
                    "frontend_ui": _HandoffProposalAdapter("frontend_ui", frontend_calls, target_crew="qa_reviewer"),
                    "qa_reviewer": _ProposalAdapter("qa_reviewer", qa_calls),
                },
                require_crew_adapters=True,
            )
            result = team.execute(
                task="Build an accessible frontend UI and test review workflow",
                intent_digest="intent-handoff",
                base_prompt="base",
                memory=memory,
                invoke_agent=lambda agent, prompt: (0, "planning evidence", 0.01),
                checkpoint=checkpoint,
                run_id="durable-handoff-test",
            )
            execution = result["crew_execution"]
            self.assertEqual(execution["handoff_status"], "dispatched_pending_independent_review")
            self.assertEqual(len(execution["handoff_receipts"]), 1)
            receipt = execution["handoff_receipts"][0]
            self.assertEqual(receipt["status"], "dispatched_pending_independent_review")
            self.assertIn(receipt["handoff_id"], execution["handoff_proposals"])
            saved = checkpoint.load("durable-handoff-test")
            self.assertIsNotNone(saved)
            handoff_checkpoint = saved.state["auren_crew_handoff_checkpoint_data"]
            self.assertEqual(len(handoff_checkpoint["in_flight"]), 1)
            self.assertEqual(handoff_checkpoint["in_flight"][0]["handoff_id"], receipt["handoff_id"])
            self.assertEqual(
                saved.state["auren_retry_budget"]["digest"],
                handoff_checkpoint["retry_budget"]["digest"],
                "outer task checkpoint must not lag the retry ledger nested in the handoff checkpoint",
            )
            self.assertEqual(len(qa_calls), 2, "QA is invoked once for its selected proposal and once for the explicit handoff")

    def test_trusted_independent_handoff_reviewer_completes_and_receipts_the_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-handoff-reviewed")
            checkpoint = InMemoryCheckpointStore()
            reviewer_requests = []

            def independent_review(request):
                reviewer_requests.append(dict(request))
                return {
                    "schema_version": "auren-handoff-review-receipt-v1",
                    "reviewer_id": "host-independent-qa",
                    "reviewer_version": "reviewer-v1",
                    "status": "pass",
                    "evidence_refs": [
                        "test://independent-qa/review-001",
                        request["source_proposal"]["proposal_digest"],
                        request["target_proposal"]["proposal_digest"],
                    ],
                    "reason": "Separate reviewer accepted the handoff proposal.",
                }

            team = GraphAgentTeam(
                [AgentSpec("planner", "planner")],
                crew_adapters={
                    "frontend_ui": _HandoffProposalAdapter("frontend_ui", [], target_crew="qa_reviewer"),
                    "qa_reviewer": _ProposalAdapter("qa_reviewer", []),
                },
                require_crew_adapters=True,
                independent_handoff_reviewer=independent_review,
                independent_handoff_reviewer_id="host-independent-qa",
                independent_handoff_reviewer_version="reviewer-v1",
            )
            result = team.execute(
                task="Build an accessible frontend UI and test review workflow",
                intent_digest="intent-handoff-reviewed",
                base_prompt="base",
                memory=memory,
                invoke_agent=lambda agent, prompt: (0, "planning evidence", 0.01),
                checkpoint=checkpoint,
                run_id="reviewed-handoff-test",
            )
            execution = result["crew_execution"]
            self.assertEqual(execution["handoff_status"], "reviewed")
            receipt = execution["handoff_receipts"][0]
            self.assertEqual(receipt["status"], "completed_independent_review")
            self.assertEqual(receipt["independent_review"]["reviewer_id"], "host-independent-qa")
            self.assertIn(reviewer_requests[0]["source_proposal"]["proposal_digest"], receipt["independent_review"]["evidence_refs"])
            self.assertIn(reviewer_requests[0]["target_proposal"]["proposal_digest"], receipt["independent_review"]["evidence_refs"])
            self.assertEqual(len(receipt["independent_review"]["digest"]), 64)
            self.assertEqual(reviewer_requests[0]["schema_version"], "auren-handoff-review-request-v1")
            self.assertIn("source_proposal", reviewer_requests[0])
            saved = checkpoint.load("reviewed-handoff-test")
            self.assertEqual(len(saved.state["auren_crew_handoff_checkpoint_data"]["completed"]), 1)
            self.assertEqual(saved.state["auren_crew_handoff_checkpoint_data"]["in_flight"], [])

    def test_independent_handoff_rejection_is_terminal_and_blocks_graph_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-handoff-rejected")
            checkpoint = InMemoryCheckpointStore()

            def independent_review(_request):
                return {
                    "schema_version": "auren-handoff-review-receipt-v1",
                    "reviewer_id": "host-independent-qa",
                    "reviewer_version": "reviewer-v1",
                    "status": "reject",
                    "evidence_refs": [
                        "test://independent-qa/review-rejected-001",
                        _request["source_proposal"]["proposal_digest"],
                        _request["target_proposal"]["proposal_digest"],
                    ],
                    "reason": "Required acceptance evidence is missing.",
                }

            team = GraphAgentTeam(
                [AgentSpec("planner", "planner")],
                crew_adapters={
                    "frontend_ui": _HandoffProposalAdapter("frontend_ui", [], target_crew="qa_reviewer"),
                    "qa_reviewer": _ProposalAdapter("qa_reviewer", []),
                },
                require_crew_adapters=True,
                independent_handoff_reviewer=independent_review,
                independent_handoff_reviewer_id="host-independent-qa",
                independent_handoff_reviewer_version="reviewer-v1",
            )
            result = team.execute(
                task="Build an accessible frontend UI and test review workflow",
                intent_digest="intent-handoff-rejected",
                base_prompt="base",
                memory=memory,
                invoke_agent=lambda agent, prompt: (0, "planning evidence", 0.01),
                checkpoint=checkpoint,
                run_id="rejected-handoff-test",
            )
            execution = result["crew_execution"]
            self.assertEqual(execution["handoff_status"], "review_rejected")
            self.assertEqual(execution["status"], "blocked")
            receipt = execution["handoff_receipts"][0]
            self.assertEqual(receipt["status"], "review_rejected")
            self.assertEqual(receipt["independent_review"]["status"], "reject")
            saved = checkpoint.load("rejected-handoff-test")
            terminal = saved.state["auren_crew_handoff_checkpoint_data"]
            self.assertEqual(terminal["queued"], [])
            self.assertEqual(terminal["in_flight"], [])
            self.assertEqual(len(terminal["completed_ids"]), 1)
            self.assertEqual(next(iter(terminal["completion_receipts"].values()))["status"], "reject")

    def test_independent_reviewer_cannot_reuse_a_specialist_crew_identity(self):
        with self.assertRaisesRegex(ValueError, "distinct from every specialist crew"):
            GraphAgentTeam(
                [AgentSpec("planner", "planner")],
                independent_handoff_reviewer=lambda _request: {},
                independent_handoff_reviewer_id="qa_reviewer",
                independent_handoff_reviewer_version="reviewer-v1",
            )

    def test_handoff_request_fails_closed_without_canonical_checkpoint_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-handoff-no-store")
            team = GraphAgentTeam(
                [AgentSpec("planner", "planner")],
                crew_adapters={
                    "frontend_ui": _HandoffProposalAdapter("frontend_ui", [], target_crew="qa_reviewer"),
                    "qa_reviewer": _ProposalAdapter("qa_reviewer", []),
                },
                require_crew_adapters=True,
            )
            result = team.execute(
                task="Build an accessible frontend UI and test review workflow",
                intent_digest="intent-handoff-no-store",
                base_prompt="base",
                memory=memory,
                invoke_agent=lambda agent, prompt: (0, "planning evidence", 0.01),
            )
            self.assertEqual(result["crew_execution"]["handoff_status"], "blocked")
            self.assertEqual(result["crew_execution"]["status"], "blocked")
            self.assertFalse(result["accepted"])

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
                require_crew_adapters=False,
            )

            self.assertFalse(result["accepted"])
            self.assertEqual(result["crew_execution"]["status"], "blocked")
            self.assertTrue(result["crew_execution"]["missing_adapters"])
            self.assertEqual(calls, ["planner"])
            self.assertNotIn("builder", calls)

    def test_resume_rejects_changed_crew_adapter_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-resume")
            from portable.agency_state_graph import InMemoryCheckpointStore
            store = InMemoryCheckpointStore()
            first_calls = []
            first_adapter = _ProposalAdapter("frontend_ui", first_calls)
            team = GraphAgentTeam(
                [AgentSpec("planner", "planner")],
                crew_adapters={"frontend_ui": first_adapter},
            )
            with self.assertRaises(RuntimeError):
                team.execute(
                    task="Build a frontend UI component",
                    intent_digest="intent-resume",
                    base_prompt="base instructions",
                    memory=memory,
                    invoke_agent=lambda agent, prompt: (0, "done", 0.01),
                    checkpoint=store,
                    run_id="crew-resume",
                    max_steps=1,
                )
            second_calls = []
            second_adapter = _ProposalAdapter("frontend_ui", second_calls)
            second_adapter.configuration_digest = "changed-adapter-config"
            second_adapter.implementation_version = "test-v2"
            changed_team = GraphAgentTeam(
                [AgentSpec("planner", "planner")],
                crew_adapters={"frontend_ui": second_adapter},
            )
            with self.assertRaisesRegex(ValueError, "refusing stale checkpoint resume"):
                changed_team.execute(
                    task="Build a frontend UI component",
                    intent_digest="intent-resume",
                    base_prompt="base instructions",
                    memory=memory,
                    invoke_agent=lambda agent, prompt: (0, "done", 0.01),
                    checkpoint=store,
                    resume=True,
                    run_id="crew-resume",
                )
            self.assertEqual(len(first_calls), 1)
            self.assertEqual(second_calls, [])

    def test_runtime_phase_factory_injects_registered_adapters_and_requires_high_risk_crew_gate(self):
        runtime._CREW_ADAPTERS.clear()
        try:
            calls = []
            adapter = _ProposalAdapter("frontend_ui", calls)
            register_crew_adapters({"frontend_ui": adapter})
            team = runtime._phase_team(
                {"risk": "high"},
                "execute",
                (AgentSpec, GraphAgentTeam),
            )
            self.assertIs(team.crew_adapters["frontend_ui"], adapter)
            self.assertTrue(team.require_crew_adapters)
        finally:
            runtime._CREW_ADAPTERS.clear()

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
