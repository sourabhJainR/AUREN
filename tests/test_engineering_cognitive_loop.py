import unittest
import tempfile
from pathlib import Path

from portable.architecture_contract import ArchitectureComponent, ArchitectureContractEngine
from portable.end_to_end_engineering_episode import EngineeringEpisodeRequest
from portable.engineering_quality_gate import QualityGate
from portable.full_stack_contract import ContractEndpoint, FullStackContractEngine
from portable.general_intelligence_cycle import GeneralIntelligenceCycle
from portable.implementation_plan import ImplementationPlanner, WorkStep
from portable.performance_budget import PerformanceBudget, PerformanceObservation
from portable.persistent_memory import PersistentMemory
from portable.requirement_contract import Requirement, RequirementContractEngine
from portable.security_engineering_gate import Threat
from portable.whole_system_engineering import EngineeringEvaluation
from portable.engineering_traceability import TraceLink


class EngineeringCognitiveLoopTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.memory = PersistentMemory(Path(self.tempdir.name) / "memory.sqlite3")

    def request(self):
        req = RequirementContractEngine().build(
            "ship engineering feature",
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
        return EngineeringEpisodeRequest("ship engineering feature", req, arch, plan, full)

    def engineering(self, task):
        return EngineeringEvaluation(task, .95, True, (f"eng:{task.task_id}",))

    def test_verified_episode_enters_learning(self):
        cycle = GeneralIntelligenceCycle.__new__(GeneralIntelligenceCycle)
        from portable.world_mega_model import WorldMegaModel
        cycle.__init__(WorldMegaModel(self.memory, "test-project"))
        result = cycle.run_engineering_episode(
            cycle_id="c1",
            intent="ship engineering feature",
            observation=__import__("portable.world_model", fromlist=["Observation"]).Observation(
                "obs", "repo", "state", "ready", "test"
            ),
            request=self.request(),
            executor=lambda request: {"changed": True},
            quality_gates=(QualityGate("tests", True, ("test:e2e",)),),
            threats=(Threat("T1", "backend", "input attack", "validation", True),),
            performance_budget=PerformanceBudget(100, 10, 512, 80),
            performance_observation=PerformanceObservation(50, 20, 256, 40),
            trace_links=(TraceLink("R1", ("impl:1",), ("test:1",), ("trace:R1",)),),
            engineering_evaluator=self.engineering,
        )
        self.assertTrue(result.accepted)
        self.assertIsNotNone(result.engineering_episode)
        self.assertIsNotNone(result.learning)
        self.assertTrue(result.learning.verified)

    def test_failed_episode_does_not_promote_learning(self):
        cycle = GeneralIntelligenceCycle.__new__(GeneralIntelligenceCycle)
        from portable.world_mega_model import WorldMegaModel
        cycle.__init__(WorldMegaModel(self.memory, "test-project"))
        result = cycle.run_engineering_episode(
            cycle_id="c2",
            intent="ship engineering feature",
            observation=__import__("portable.world_model", fromlist=["Observation"]).Observation(
                "obs-failed", "repo", "state", "ready", "test"
            ),
            request=self.request(),
            executor=lambda request: {"changed": True},
            quality_gates=(QualityGate("tests", False, (), ("failure",)),),
            threats=(Threat("T1", "backend", "input attack", "validation", True),),
            performance_budget=PerformanceBudget(100, 10, 512, 80),
            performance_observation=PerformanceObservation(50, 20, 256, 40),
            trace_links=(TraceLink("R1", ("impl:1",), ("test:1",), ("trace:R1",)),),
            engineering_evaluator=self.engineering,
        )
        self.assertFalse(result.accepted)
        self.assertIsNone(result.learning)
        self.assertEqual(result.next_action, "repair")


if __name__ == "__main__":
    unittest.main()
