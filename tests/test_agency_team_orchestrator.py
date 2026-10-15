from __future__ import annotations
import unittest
from portable.agency_team_orchestrator import AgentOutput, AgentTeamOrchestrator, WorkUnit
from portable.action_governance import ActionGovernanceGateway, ActionPolicy

class AgentTeamOrchestratorTests(unittest.TestCase):
    def test_team_spawns_parallel_research_and_gates_before_dependents(self):
        units = (
            WorkUnit("research", "inspect architecture", specialist="explorer", read_paths=("src",)),
            WorkUnit("tests", "inspect tests", specialist="tester", read_paths=("tests",)),
            WorkUnit("implement", "implement change", specialist="builder", role="primary", mutation_mode="bounded", read_paths=("src",), write_paths=("src",), depends_on=("research", "tests")),
        )
        calls = []
        def spawn(unit, evidence):
            calls.append((unit.unit_id, evidence))
            return AgentOutput(unit.unit_id, "done", evidence=(unit.goal,))
        def verify(unit, output, evidence): return True, f"v-{unit.unit_id}"
        def review(unit, output, evidence, verification): return True, f"r-{unit.unit_id}"
        run = AgentTeamOrchestrator(max_parallel=2).run(units, evidence_digest="E1", spawn=spawn, verify=verify, review=review)
        self.assertTrue(run.passed)
        self.assertEqual(run.evidence_digest, "E1")
        self.assertEqual({x[1] for x in calls}, {"E1"})
        self.assertEqual(len(run.gates), 3)
        self.assertTrue(all(g.review_passed for g in run.gates))
        self.assertLess(max(calls.index(("research", "E1")), calls.index(("tests", "E1"))), calls.index(("implement", "E1")))
    def test_failed_early_review_stops_dependent_work(self):
        units = (
            WorkUnit("inspect", "inspect", specialist="explorer"),
            WorkUnit("change", "change", specialist="builder", mutation_mode="bounded", read_paths=("src",), write_paths=("src",), depends_on=("inspect",)),
        )
        spawned = []
        def spawn(unit, evidence): spawned.append(unit.unit_id); return AgentOutput(unit.unit_id, "done")
        def verify(unit, output, evidence): return True, "v"
        def review(unit, output, evidence, verification): return (unit.unit_id != "inspect"), "r"
        run = AgentTeamOrchestrator().run(units, evidence_digest="E2", spawn=spawn, verify=verify, review=review)
        self.assertFalse(run.passed)
        self.assertEqual(spawned, ["inspect"])
        self.assertIn("change", run.stopped_units)
    def test_failed_verification_stops_dependent_work(self):
        units = (
            WorkUnit("a", "a", specialist="a"),
            WorkUnit("b", "b", specialist="b", depends_on=("a",)),
        )
        spawned = []
        def spawn(unit, evidence): spawned.append(unit.unit_id); return AgentOutput(unit.unit_id, "done")
        def verify(unit, output, evidence): return unit.unit_id != "a", "verification"
        def review(unit, output, evidence, verification): return True, "review"
        run = AgentTeamOrchestrator().run(units, evidence_digest="E3", spawn=spawn, verify=verify, review=review)
        self.assertFalse(run.passed)
        self.assertEqual(spawned, ["a"])
        self.assertIn("b", run.stopped_units)

    def test_governance_gateway_blocks_protected_specialist_scope_before_spawn(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
            protected_targets=(".github/workflows",),
        ))
        units = (WorkUnit("inspect", "inspect", specialist="explorer", read_paths=(".github/workflows",)),)
        spawned = []
        def spawn(unit, evidence):
            spawned.append(unit.unit_id)
            return AgentOutput(unit.unit_id, "done")
        run = AgentTeamOrchestrator().run(
            units, evidence_digest="E4", spawn=spawn,
            verify=lambda unit, output, evidence: (True, "v"),
            review=lambda unit, output, evidence, verification: (True, "r"),
            governance_gateway=gateway, run_id="governed-run",
        )
        self.assertFalse(run.passed)
        self.assertEqual(spawned, [])
        self.assertEqual(run.outputs[0].status, "blocked")
        self.assertEqual(run.outputs[0].findings, ("trusted_approval_missing_or_invalid",))
        self.assertTrue(gateway.verify_trace()["passed"])

    def test_governance_gateway_allows_scoped_specialist_and_records_execution(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        units = (WorkUnit("inspect", "inspect", specialist="explorer", read_paths=("src",)),)
        spawned = []
        def spawn(unit, evidence):
            spawned.append(unit.unit_id)
            return AgentOutput(unit.unit_id, "done")
        run = AgentTeamOrchestrator().run(
            units, evidence_digest="E5", spawn=spawn,
            verify=lambda unit, output, evidence: (True, "v"),
            review=lambda unit, output, evidence, verification: (True, "r"),
            governance_gateway=gateway, run_id="governed-run-2",
        )
        self.assertTrue(run.passed)
        self.assertEqual(spawned, ["inspect"])
        self.assertTrue(gateway.verify_trace()["passed"])
        self.assertIn("action_completed", [event["event"] for event in gateway.events])

if __name__ == "__main__": unittest.main()
