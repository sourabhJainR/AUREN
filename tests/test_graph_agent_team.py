import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from portable.agency_state_graph import Checkpoint, InMemoryCheckpointStore
from portable.task_planner import TaskPlan
from portable.benchmark_task_dispatch import BenchmarkExecutionRequest
from portable.engineering_design_guard import PRINCIPLE_FAMILY_IDS
from portable.adaptive_resource_handling import bounded_text, iter_text_chunks, sample_items


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / ".ai-harness"
sys.path.insert(0, str(HARNESS))

from runtime.graph_agent_team import AgentSpec, GraphAgentTeam, SharedTaskMemory, team_for_route


def principle_verification_receipt(intent_digest: str, code_revision: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "auren-engineering-principle-verification-v1",
        "intent_digest": intent_digest,
        "code_revision": code_revision,
        "reviewer_id": "independent-reviewer",
        "reviewer_version": "reviewer-v1",
        "families": {
            family_id: {
                "status": "verified",
                "reason": f"Verified {family_id} against the final revision.",
                "evidence_refs": [f"evidence:{family_id}:{code_revision}"],
            }
            for family_id in PRINCIPLE_FAMILY_IDS
        },
    }
    payload["digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


class AdaptiveResourceHandlingTests(unittest.TestCase):
    def test_chunking_is_bounded_and_round_trips_streamed_text(self):
        source = "0123456789" * 1700
        chunks = list(iter_text_chunks(source, 257))
        self.assertTrue(all(0 < len(chunk) <= 257 for chunk in chunks))
        self.assertEqual("".join(chunks), source)

    def test_bounded_text_is_deterministic_and_marks_lossy_sampling(self):
        source = "START\n" + ("ordinary detail\n" * 500) + "SECURITY: preserve this constraint\n" + ("tail detail\n" * 500) + "END"
        first, strategy = bounded_text(source, 500)
        second, second_strategy = bounded_text(source, 500)
        self.assertEqual(first, second)
        self.assertEqual(strategy, "head_tail_critical_sample")
        self.assertEqual(strategy, second_strategy)
        self.assertLessEqual(len(first), 500)
        self.assertIn("adaptive resource handling", first)
        self.assertTrue(first.startswith("START"))
        self.assertTrue(first.endswith("END"))

    def test_sampling_is_deterministic_and_preserves_edges(self):
        self.assertEqual(sample_items([str(i) for i in range(10)], 3), ["0", "4", "9"])
        self.assertEqual(sample_items(["a", "b"], 5), ["a", "b"])

    def test_shared_memory_records_oversized_entry_adaptation(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "oversized-entry", max_chars=1400)
            original = "BEGIN\n" + ("large tool output and ordinary context\n" * 2000) + "END"
            item_id = memory.publish(agent="worker", role="qa", kind="tool-output", text=original,
                                      evidence=["run:123"], confidence=0.8)
            rows = memory.snapshot(10)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["id"], item_id)
            self.assertTrue(row["text_truncated"])
            self.assertEqual(row["text_original_chars"], len(original))
            self.assertNotEqual(row["text_strategy"], "complete")
            self.assertIn("adaptive resource handling", row["text"])
            self.assertLessEqual(memory._size(rows), memory.max_chars)
            compact = memory.compact_text(limit=300)
            self.assertLessEqual(len(compact), 300)


class GraphAgentTeamTests(unittest.TestCase):
    def test_resume_migrates_legacy_checkpoint_keys_before_runtime_reads_and_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = InMemoryCheckpointStore()
            checkpoint.save(Checkpoint(
                run_id="legacy-key-migration",
                step=0,
                state={
                    "aer_execution_strategy": {"name": "default"},
                    "aer_execution_mode": {"name": "balanced"},
                },
                next_nodes=("planner",),
            ))
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "legacy-key-migration")
            team = GraphAgentTeam([AgentSpec("planner", "planner")])
            team.execute(
                task="Resume legacy checkpoint safely",
                intent_digest="legacy-key-migration",
                base_prompt="base",
                memory=memory,
                invoke_agent=lambda agent, prompt: (0, "planning complete", 0.01),
                checkpoint=checkpoint,
                resume=True,
                run_id="legacy-key-migration",
            )
            saved = checkpoint.load("legacy-key-migration")
            self.assertNotIn("aer_execution_strategy", saved.state)
            self.assertNotIn("aer_execution_mode", saved.state)
            self.assertEqual(saved.state["auren_execution_strategy"], {"name": "default"})
            self.assertEqual(saved.state["auren_execution_mode"], {"name": "balanced"})
            self.assertEqual(saved.state["auren_state_schema_version"], 2)

    def test_high_risk_task_requires_exact_revision_and_emits_principle_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            invoked = []
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "principle-gate")
            team = GraphAgentTeam([
                AgentSpec("planner", "planner", read_only=True),
                AgentSpec("builder", "builder", depends_on=("planner",), read_only=False, critical=True),
            ])
            result = team.execute(
                task="Change authorization handling in an API",
                intent_digest="principle-gate",
                base_prompt="base",
                memory=memory,
                invoke_agent=lambda agent, prompt: (invoked.append(agent.name) or 0, "evidence", 0.01),
                risk="high",
                changed_paths=["portable/auth.py"],
                design_contract={"architecture": "reuse the existing authorization boundary"},
            )
            record = result["principle_evidence"]
            self.assertEqual(record["schema_version"], "auren-engineering-principle-evidence-v1")
            self.assertEqual(len(record["families"]), 12)
            self.assertEqual(record["status"], "blocked")
            self.assertFalse(record["exact_revision_bound"])
            self.assertIn("high-risk code change requires an exact code_revision", record["blockers"])
            self.assertNotIn("builder", invoked)
            self.assertFalse(result["accepted"])

    def test_final_review_requires_exact_revision_principle_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "final-review-intent")
            team = GraphAgentTeam([
                AgentSpec("reviewer", "reviewer", read_only=True, critical=True),
            ])
            common = {
                "task": "Review final source revision",
                "intent_digest": "final-review-intent",
                "base_prompt": "Review only; do not mutate files.",
                "memory": memory,
                "invoke_agent": lambda agent, prompt: (0, "review completed", 0.01),
                "risk": "medium",
                "changed_paths": ["portable/helper.py"],
                "design_contract": {"architecture": "reuse the existing helper boundary"},
                "code_revision": "revision-123",
                "final_verification": True,
                "require_principle_verification": True,
            }
            missing = team.execute(**common)
            self.assertFalse(missing["accepted"])
            self.assertEqual(missing["principle_evidence"]["status"], "blocked")
            self.assertEqual(missing["principle_verification"]["status"], "blocked")

            verified = team.execute(
                **common,
                principle_verification=principle_verification_receipt(
                    "final-review-intent", "revision-123"
                ),
            )
            self.assertTrue(verified["accepted"], verified.get("principle_evidence"))
            self.assertEqual(verified["principle_evidence"]["status"], "verified")
            self.assertEqual(verified["principle_verification"]["status"], "passed")

    def test_autonomous_campaign_learning_is_runtime_bound_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")
            request = BenchmarkExecutionRequest(
                task_id="benchmark:coding:1", domain="coding", holdout=True,
                objective="test coding", risk_budget=.2, resource_budget=.5,
                required_evidence=("canonical execution evidence",),
            )
            team = GraphAgentTeam([AgentSpec("planner", "planner")])

            def fake_execute(request, **kwargs):
                return {
                    "benchmark_execution_receipt": {
                        "task_id": request.task_id,
                        "domain": request.domain,
                        "holdout": request.holdout,
                        "evidence_ids": ("run:evidence",),
                        "evidence_kinds": ("canonical execution evidence",),
                        "required_evidence": request.required_evidence,
                        "success": True,
                        "verified": True,
                        "reason": "accepted",
                    },
                    "autonomy_benchmark": {"overall": .9},
                }

            team.execute_benchmark_request = fake_execute
            result = team.execute_autonomous_benchmark_learning_campaign(
                requests=(request,),
                max_tasks=1,
                retest_max_tasks=1,
                intent_digest="intent-a",
                base_prompt="base",
                memory=memory,
                invoke_agent=lambda agent, prompt: (0, agent.name, 0.01),
                safety_evidence_verified=True,
            )
            self.assertEqual(result["initial"]["selected_task_ids"], [request.task_id])
            self.assertEqual(result["initial"]["observations"][0]["realized_score"], .9)
            self.assertTrue(result["initial"]["observations"][0]["realized_success"])

    def test_campaign_intervention_drives_cross_domain_holdout_retest(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")
            coding = BenchmarkExecutionRequest(
                task_id="benchmark:coding:1", domain="coding", holdout=True,
                objective="test coding", risk_budget=.2, resource_budget=.5,
                required_evidence=("canonical execution evidence",),
            )
            research = BenchmarkExecutionRequest(
                task_id="benchmark:research:1", domain="research", holdout=True,
                objective="test research", risk_budget=.2, resource_budget=.5,
                required_evidence=("canonical execution evidence",),
            )
            team = GraphAgentTeam([AgentSpec("planner", "planner")])

            def fake_execute(request, **kwargs):
                failed = request.task_id == coding.task_id
                return {
                    "benchmark_execution_receipt": {
                        "task_id": request.task_id,
                        "domain": request.domain,
                        "holdout": request.holdout,
                        "evidence_ids": ("run:evidence",),
                        "evidence_kinds": ("canonical execution evidence",),
                        "required_evidence": request.required_evidence,
                        "success": not failed,
                        "verified": not failed,
                        "reason": "verification failed" if failed else "accepted",
                    },
                    "autonomy_benchmark": {"overall": .2 if failed else .9},
                    "active_learning": {"self_model": {"uncertainty": .1}},
                    "execution_mode": {"selected": "balanced", "baseline": "balanced"},
                    "execution_strategy": {"selected": "default", "baseline": "default"},
                    "agents": {},
                }

            team.execute_benchmark_request = fake_execute
            result = team.execute_autonomous_benchmark_learning_campaign(
                requests=(coding, research),
                max_tasks=1,
                retest_max_tasks=1,
                intent_digest="intent-a",
                base_prompt="base",
                memory=memory,
                invoke_agent=lambda agent, prompt: (0, agent.name, 0.01),
                safety_evidence_verified=True,
            )
            self.assertEqual(result["initial"]["selected_task_ids"], [coding.task_id])
            self.assertEqual(len(result["retest"]["selected_task_ids"]), 1)
            self.assertTrue(result["retest"]["selected_task_ids"][0].startswith("benchmark:research:"))
            self.assertNotEqual(result["retest"]["selected_task_ids"][0], coding.task_id)
            self.assertEqual(result["retest"]["observations"][0]["task_id"], result["retest"]["selected_task_ids"][0])

    def test_shared_memory_is_scoped_to_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.jsonl"
            first = SharedTaskMemory(path, "intent-a")
            second = SharedTaskMemory(path, "intent-b")
            first.publish(agent="planner", role="planner", kind="fact", text="only task A", confidence=1.0)
            self.assertEqual(len(first.snapshot()), 1)
            self.assertEqual(second.snapshot(), [])

    def test_dependency_levels_and_parallel_read_only_roles(self):
        team = GraphAgentTeam([
            AgentSpec("planner", "planner"),
            AgentSpec("explorer", "explorer", depends_on=("planner",)),
            AgentSpec("researcher", "researcher", depends_on=("planner",)),
            AgentSpec("builder", "builder", depends_on=("explorer", "researcher"), read_only=False),
        ])
        levels = [[agent.name for agent in level] for level in team.levels()]
        self.assertEqual(levels[0], ["planner"])
        self.assertEqual(set(levels[1]), {"explorer", "researcher"})
        self.assertEqual(levels[2], ["builder"])

    def test_graph_plan_is_canonical_task_plan(self):
        team = GraphAgentTeam([
            AgentSpec("planner", "planner"),
            AgentSpec("builder", "builder", depends_on=("planner",), read_only=False),
        ])
        self.assertIsInstance(team._plan, TaskPlan)
        self.assertEqual(team._plan.tasks["builder"].dependencies, ["planner"])
        self.assertEqual([[agent.name for agent in level] for level in team.levels()], [["planner"], ["builder"]])

    def test_team_passes_shared_memory_to_downstream_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")
            seen = {}

            def invoke(agent, prompt):
                seen[agent.name] = prompt
                return 0, f"finding from {agent.name}", 0.01

            team = GraphAgentTeam([
                AgentSpec("planner", "planner"),
                AgentSpec("builder", "builder", depends_on=("planner",), read_only=False),
            ])
            result = team.execute(task="implement X", intent_digest="intent-a", base_prompt="base", memory=memory, invoke_agent=invoke)
            self.assertTrue(result["accepted"])
            self.assertIn("finding from planner", seen["builder"])
            self.assertEqual(result["shared_memory_entries"], 2)
            self.assertEqual(result["execution_trace"], ["planner", "builder"])

    def test_read_only_dependencies_run_in_parallel_through_state_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")
            active = 0
            peak = 0
            lock = threading.Lock()
            rendezvous = threading.Barrier(2, timeout=2.0)

            def invoke(agent, prompt):
                nonlocal active, peak
                if agent.name in {"explorer", "researcher"}:
                    with lock:
                        active += 1
                        peak = max(peak, active)
                    try:
                        rendezvous.wait()
                    finally:
                        with lock:
                            active -= 1
                return 0, agent.name, 0.01

            team = GraphAgentTeam([
                AgentSpec("planner", "planner"),
                AgentSpec("explorer", "explorer", depends_on=("planner",)),
                AgentSpec("researcher", "researcher", depends_on=("planner",)),
                AgentSpec("builder", "builder", depends_on=("explorer", "researcher"), read_only=False),
            ], max_parallel_read_only=2)
            result = team.execute(task="X", intent_digest="intent-a", base_prompt="base", memory=memory, invoke_agent=invoke)
            self.assertTrue(result["accepted"])
            self.assertEqual(peak, 2)
            published_agents = {row["agent"] for row in memory.snapshot()}
            self.assertTrue({"planner", "explorer", "researcher", "builder"}.issubset(published_agents))

    def test_parallel_eligibility_excludes_learning_and_local_commands(self):
        team = GraphAgentTeam([
            AgentSpec("reader", "reader", read_only=True),
            AgentSpec("writer", "writer", read_only=False),
            AgentSpec("local-reader", "reader", read_only=True, local_command=("python", "-c", "pass")),
            AgentSpec("learning-steward", "learning", read_only=True),
        ])
        self.assertTrue(team._parallel_safe("reader"))
        self.assertFalse(team._parallel_safe("writer"))
        self.assertFalse(team._parallel_safe("local-reader"))
        self.assertFalse(team._parallel_safe("learning-steward"))

    def test_checkpoint_resume_does_not_repeat_completed_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")
            store = InMemoryCheckpointStore()
            calls = []

            def invoke(agent, prompt):
                calls.append(agent.name)
                return 0, agent.name, 0.01

            team = GraphAgentTeam([
                AgentSpec("planner", "planner"),
                AgentSpec("builder", "builder", depends_on=("planner",), read_only=False),
            ])
            with self.assertRaises(RuntimeError):
                team.execute(task="X", intent_digest="intent-a", base_prompt="base", memory=memory,
                             invoke_agent=invoke, checkpoint=store, run_id="resume-1", max_steps=1)
            self.assertEqual(calls, ["planner"])
            resumed = team.execute(task="X", intent_digest="intent-a", base_prompt="base", memory=memory,
                                   invoke_agent=invoke, checkpoint=store, resume=True, run_id="resume-1")
            self.assertTrue(resumed["accepted"])
            self.assertEqual(calls, ["planner", "builder"])
            self.assertEqual(resumed["execution_trace"], ["planner", "builder"])

    def test_resume_rejects_stale_intent_digest_without_crew_adapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = InMemoryCheckpointStore()
            calls = []

            def invoke(agent, prompt):
                calls.append(agent.name)
                return 0, agent.name, 0.01

            team = GraphAgentTeam([
                AgentSpec("planner", "planner"),
                AgentSpec("builder", "builder", depends_on=("planner",), read_only=False),
            ])
            memory_a = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")
            with self.assertRaises(RuntimeError):
                team.execute(
                    task="X", intent_digest="intent-a", base_prompt="base", memory=memory_a,
                    invoke_agent=invoke, checkpoint=store, run_id="stale-intent", max_steps=1,
                )

            memory_b = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-b")
            with self.assertRaisesRegex(ValueError, "stale checkpoint resume"):
                team.execute(
                    task="X", intent_digest="intent-b", base_prompt="base", memory=memory_b,
                    invoke_agent=invoke, checkpoint=store, resume=True, run_id="stale-intent",
                )
            self.assertEqual(calls, ["planner"])

    def test_resume_rejects_stale_task_plan_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = InMemoryCheckpointStore()
            calls = []

            def invoke(agent, prompt):
                calls.append(agent.name)
                return 0, agent.name, 0.01

            original = GraphAgentTeam([
                AgentSpec("planner", "planner"),
                AgentSpec("builder", "builder", depends_on=("planner",), read_only=False),
            ])
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")
            with self.assertRaises(RuntimeError):
                original.execute(
                    task="X", intent_digest="intent-a", base_prompt="base", memory=memory,
                    invoke_agent=invoke, checkpoint=store, run_id="stale-plan", max_steps=1,
                )

            changed = GraphAgentTeam([
                AgentSpec("planner", "planner"),
                AgentSpec("builder", "builder", read_only=False),
            ])
            with self.assertRaisesRegex(ValueError, "stale checkpoint resume"):
                changed.execute(
                    task="X", intent_digest="intent-a", base_prompt="base", memory=memory,
                    invoke_agent=invoke, checkpoint=store, resume=True, run_id="stale-plan",
                )
            self.assertEqual(calls, ["planner"])

    def test_route_creates_builder_verifier_and_reviews(self):
        team = team_for_route({"mode": "implement", "capabilities": [], "risk": "high"})
        names = set(team.agents)
        self.assertTrue(team.require_crew_adapters)
        self.assertTrue({"planner", "explorer", "builder", "verifier", "correctness-reviewer", "security-reviewer", "architecture-reviewer", "synthesizer"}.issubset(names))

    def test_resource_routing_exposes_adaptive_inference_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = __import__("portable.local_offload", fromlist=["LocalOffloadBroker"]).LocalOffloadBroker(
                Path(tmp),
            )
            team = GraphAgentTeam([
                AgentSpec(
                    "verifier",
                    "verifier",
                    local_command=("python", "-m", "pytest", "-q"),
                    estimated_duration_seconds=5.0,
                    estimated_memory_mb=128,
                    evidence_value=0.95,
                ),
            ])
            decision = team._resource_decision(team.agents["verifier"], broker)
            self.assertIn(decision.inference_depth, {"minimal", "standard", "deep", "human"})
            self.assertEqual(decision.inference_depth, "standard")

    def test_cycle_is_rejected(self):
        with self.assertRaises(ValueError):
            GraphAgentTeam([
                AgentSpec("a", "a", depends_on=("b",)),
                AgentSpec("b", "b", depends_on=("a",)),
            ])



    def test_skill_execution_plan_telemetry_is_bounded_and_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")

            def invoke(agent, prompt):
                return 0, agent.name, 0.01

            team = GraphAgentTeam([
                AgentSpec("planner", "planner", capabilities=("delegate_task",)),
            ])
            result = team.execute(
                task="plan X",
                intent_digest="intent-a",
                base_prompt="base",
                memory=memory,
                invoke_agent=invoke,
            )
            plan = result["agents"]["planner"]["capability_execution_plan"]
            self.assertEqual(plan["selected_skill_count"], plan["executed_skill_count"])
            self.assertEqual(plan["pruned_secondary_count"], 0)
            self.assertLessEqual(plan["instruction_chars"], plan["context_budget_chars"])
            self.assertEqual(plan["planned_parallelism"], 1)
            self.assertEqual(len(plan["groups"]), 1)


    def test_crew_proposals_are_bound_to_canonical_evidence_envelope(self):
        import hashlib
        import json

        class FrontendProposal:
            crew_id = "frontend_ui"
            implementation_version = "test-v1"
            configuration_digest = "frontend-config-v1"

            def propose(self, request):
                return {
                    "schema_version": "auren-crew-proposal-v1",
                    "recommendation": "review required",
                    "artifacts": list(request["required_artifacts"]),
                    "evidence_refs": ["test://frontend/inspection"],
                    "uncertainty": ["Independent QA remains required."],
                    "blockers": [],
                    "acceptance_check_results": {
                        name: {"status": "not_run", "evidence_refs": []}
                        for name in request["acceptance_checks"]
                    },
                }

        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-crew-envelope")
            team = GraphAgentTeam(
                [AgentSpec("planner", "planner")],
                crew_adapters={"frontend_ui": FrontendProposal()},
            )
            result = team.execute(
                task="Implement frontend UI",
                intent_digest="intent-crew-envelope",
                base_prompt="base",
                memory=memory,
                invoke_agent=lambda agent, prompt: (0, "plan recorded", 0.01),
            )

        execution = result["crew_execution"]
        envelope = execution["evidence_envelope"]
        supplied_digest = envelope.pop("digest")
        expected_digest = hashlib.sha256(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(supplied_digest, expected_digest)
        self.assertEqual(envelope["intent_digest"], "intent-crew-envelope")
        self.assertEqual(envelope["crew_plan_digest"], result["crew_plan"]["digest"])
        self.assertIn("frontend_ui", execution["proposal_receipts"], execution.get("dispatch_failures"))
        proposal = execution["proposals"]["frontend_ui"]
        expected_receipt = hashlib.sha256(
            json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(execution["proposal_receipts"]["frontend_ui"], expected_receipt)
        self.assertEqual(execution["handoff_receipts"], [])
        self.assertEqual(execution["handoff_status"], "not_requested")

if __name__ == "__main__":
    unittest.main()
