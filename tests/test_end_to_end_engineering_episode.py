import unittest

from portable.architecture_contract import ArchitectureComponent, ArchitectureContractEngine
from portable.engineering_quality_gate import QualityGate
from portable.engineering_traceability import TraceLink
from portable.full_stack_contract import ContractEndpoint, FullStackContractEngine
from portable.implementation_plan import ImplementationPlanner, WorkStep
from portable.end_to_end_engineering_episode import (
    EndToEndEngineeringEpisode,
    EngineeringEpisodeRequest,
)
from portable.model_topology_benchmark import ModelEvaluation
from portable.performance_budget import PerformanceBudget, PerformanceObservation
from portable.requirement_contract import Requirement, RequirementContractEngine
from portable.security_engineering_gate import Threat
from portable.whole_system_engineering import EngineeringEvaluation


class EndToEndEngineeringEpisodeTests(unittest.TestCase):
    def _request(self):
        req = RequirementContractEngine().build(
            "ship a small full-stack feature",
            (Requirement("R1", "feature works", ("happy path",)),),
        )
        arch = ArchitectureContractEngine().build(
            ("R1",),
            (ArchitectureComponent("backend", "serves data", (), ("/api",)),),
            boundaries=("backend",),
            verification=("architecture-test",),
        )
        plan = ImplementationPlanner().build(
            (WorkStep("S1", "implement feature", (), ("test-feature",)),)
        )
        full = FullStackContractEngine().build(
            ("loading", "ready", "error"),
            (ContractEndpoint("GET", "/api/items", "none", "items"),),
            ("items are unique",),
            ("load -> ready",),
        )
        return EngineeringEpisodeRequest("ship a small full-stack feature", req, arch, plan, full)

    def _engineering(self, task):
        return EngineeringEvaluation(task, 0.95, True, (f"e:{task.task_id}",))

    def test_successful_episode(self):
        episode = EndToEndEngineeringEpisode()
        result = episode.run(
            self._request(),
            executor=lambda request: {"changed": True},
            quality_gates=(QualityGate("tests", True, ("test:e2e",)),),
            threats=(Threat("T1", "backend", "input attack", "validation", True),),
            performance_budget=PerformanceBudget(100, 10, 512, 80),
            performance_observation=PerformanceObservation(50, 20, 256, 40),
            trace_links=(TraceLink("R1", ("impl:1",), ("test:1",), ("e:R1",)),),
            engineering_evaluator=self._engineering,
            benchmark_models=("local", "frontier"),
            benchmark_task_ids=("task-1",),
            benchmark_evaluator=lambda model, task: ModelEvaluation(model, task, .9, True, f"b:{model}:{task}"),
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.next_action, "learn")
        self.assertTrue(result.traceability.verified)

    def test_failed_quality_routes_to_repair(self):
        episode = EndToEndEngineeringEpisode()
        result = episode.run(
            self._request(),
            executor=lambda request: {"changed": True},
            quality_gates=(QualityGate("tests", False, (), ("test failure",)),),
            threats=(Threat("T1", "backend", "input attack", "validation", True),),
            performance_budget=PerformanceBudget(100, 10, 512, 80),
            performance_observation=PerformanceObservation(50, 20, 256, 40),
            trace_links=(TraceLink("R1", ("impl:1",), ("test:1",), ("e:R1",)),),
            engineering_evaluator=self._engineering,
            repair_defect="test failure",
            repair_baseline=.5,
            repair_attempt=lambda defect, attempt: __import__(
                "portable.verified_repair_loop", fromlist=["RepairAttempt"]
            ).RepairAttempt(attempt, defect, .8, True, f"repair:{attempt}"),
        )
        self.assertTrue(result.repair.accepted)
        self.assertFalse(result.accepted)
        self.assertEqual(result.next_action, "repair")

    def test_rejects_incomplete_requirements(self):
        req = self._request()
        bad = EngineeringEpisodeRequest(
            req.intent,
            req.requirements.__class__(req.intent, (Requirement("R1", "x", ()),)),
            req.architecture,
            req.implementation_plan,
            req.full_stack,
        )
        with self.assertRaises(ValueError):
            EndToEndEngineeringEpisode().run(
                bad,
                executor=lambda _: None,
                quality_gates=(QualityGate("tests", True, ("x",)),),
                threats=(Threat("T1", "backend", "x", "y", True),),
                performance_budget=PerformanceBudget(1, 1, 1, 1),
                performance_observation=PerformanceObservation(1, 1, 1, 1),
                trace_links=(),
                engineering_evaluator=self._engineering,
            )


if __name__ == "__main__":
    unittest.main()
