from __future__ import annotations
import unittest
from portable.agency_team_orchestrator import AgentOutput, AgentTeamOrchestrator, WorkUnit
from portable.action_governance import ActionGovernanceGateway, ActionPolicy
from portable.execution_boundary import ExecutionAuthorityBinding
from portable.runtime_recovery import RuntimeExecutionGuard

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


    def _authority(self, unit_id, run_id, **overrides):
        values = dict(
            execution_id="exec-" + unit_id,
            run_id=run_id,
            task_id=unit_id,
            repository_digest="repo-digest",
            repository_revision="abc123",
            principal="explorer",
            allowed_capabilities=("repository.read",),
            allowed_actions=("specialist.execute",),
            allowed_targets=("src",),
            budget_id="budget-" + unit_id,
            max_attempts=3,
        )
        values.update(overrides)
        return ExecutionAuthorityBinding(**values)

    def test_authority_bound_team_execution_charges_unit_budget_before_spawn(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        units = (WorkUnit("inspect", "inspect", specialist="explorer", read_paths=("src",)),)
        guards = {"inspect": RuntimeExecutionGuard(max_steps=10, max_attempts=3)}
        spawned = []
        run = AgentTeamOrchestrator().run(
            units, evidence_digest="E8",
            spawn=lambda unit, evidence: spawned.append(unit.unit_id) or AgentOutput(unit.unit_id, "done"),
            verify=lambda unit, output, evidence: (True, "v"),
            review=lambda unit, output, evidence, verification: (True, "r"),
            governance_gateway=gateway, run_id="bound-run",
            execution_authorities={"inspect": self._authority("inspect", "bound-run")},
            execution_guards=guards, repository_revision="abc123", repository_digest="repo-digest",
        )
        self.assertTrue(run.passed)
        self.assertEqual(spawned, ["inspect"])
        self.assertEqual(guards["inspect"].attempts_used, 1)
        self.assertTrue(gateway.verify_trace()["passed"])

    def test_authority_bound_team_denies_out_of_scope_before_spawn(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        units = (WorkUnit("inspect", "inspect", specialist="explorer", read_paths=("src",)),)
        guards = {"inspect": RuntimeExecutionGuard(max_steps=10, max_attempts=3)}
        spawned = []
        run = AgentTeamOrchestrator().run(
            units, evidence_digest="E9",
            spawn=lambda unit, evidence: spawned.append(unit.unit_id) or AgentOutput(unit.unit_id, "done"),
            verify=lambda unit, output, evidence: (True, "v"),
            review=lambda unit, output, evidence, verification: (True, "r"),
            governance_gateway=gateway, run_id="bound-run-2",
            execution_authorities={"inspect": self._authority("inspect", "bound-run-2", allowed_targets=("docs",))},
            execution_guards=guards, repository_revision="abc123", repository_digest="repo-digest",
        )
        self.assertFalse(run.passed)
        self.assertEqual(spawned, [])
        self.assertEqual(guards["inspect"].attempts_used, 0)
        self.assertIn("target_out_of_scope", run.outputs[0].findings)

    def test_authority_bound_team_requires_complete_unit_bindings(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        units = (
            WorkUnit("one", "inspect one", specialist="explorer", read_paths=("src",)),
            WorkUnit("two", "inspect two", specialist="explorer", read_paths=("src",)),
        )
        with self.assertRaisesRegex(ValueError, "one execution authority per team unit"):
            AgentTeamOrchestrator().run(
                units, evidence_digest="E10",
                spawn=lambda unit, evidence: AgentOutput(unit.unit_id, "done"),
                verify=lambda unit, output, evidence: (True, "v"),
                review=lambda unit, output, evidence, verification: (True, "r"),
                governance_gateway=gateway, run_id="bound-run-3",
                execution_authorities={"one": self._authority("one", "bound-run-3")},
                execution_guards={
                    "one": RuntimeExecutionGuard(max_steps=5, max_attempts=2),
                    "two": RuntimeExecutionGuard(max_steps=5, max_attempts=2),
                },
                repository_revision="abc123", repository_digest="repo-digest",
            )

    def test_governance_gateway_rejects_mutating_unit_without_write_scope(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
        ))
        units = (WorkUnit("change", "change", specialist="builder", mutation_mode="bounded"),)
        spawned = []
        with self.assertRaisesRegex(ValueError, "mutating work must declare write paths"):
            AgentTeamOrchestrator().run(
                units, evidence_digest="E6",
                spawn=lambda unit, evidence: spawned.append(unit.unit_id) or AgentOutput(unit.unit_id, "done"),
                verify=lambda unit, output, evidence: (True, "v"),
                review=lambda unit, output, evidence, verification: (True, "r"),
                governance_gateway=gateway, run_id="governed-run-3",
            )
        self.assertEqual(spawned, [])


    def test_governance_requires_explicit_run_identity(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read"}),
        ))
        with self.assertRaisesRegex(ValueError, "run_id is required"):
            AgentTeamOrchestrator().run(
                (WorkUnit("inspect", "inspect", read_paths=("src",)),),
                evidence_digest="E7",
                spawn=lambda unit, evidence: AgentOutput(unit.unit_id, "done"),
                verify=lambda unit, output, evidence: (True, "v"),
                review=lambda unit, output, evidence, verification: (True, "r"),
                governance_gateway=gateway,
            )


    def test_legacy_team_path_blocks_external_effect_without_durable_ledger(self):
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
            external_effect_actions=frozenset({"specialist.execute"}),
            require_idempotency_for_external_effects=False,
        ))
        units = (WorkUnit("publish", "publish a consequential change", specialist="builder", read_paths=("src",)),)
        spawned = []
        run = AgentTeamOrchestrator().run(
            units, evidence_digest="E-external",
            spawn=lambda unit, evidence: spawned.append(unit.unit_id) or AgentOutput(unit.unit_id, "done"),
            verify=lambda unit, output, evidence: (True, "verified"),
            review=lambda unit, output, evidence, verification: (True, "reviewed"),
            governance_gateway=gateway, run_id="legacy-external-team",
        )
        self.assertFalse(run.passed)
        self.assertEqual(spawned, [])
        self.assertEqual(run.outputs[0].status, "blocked")
        self.assertEqual(run.outputs[0].findings, ("durable_idempotency_ledger_required",))


if __name__ == "__main__": unittest.main()
