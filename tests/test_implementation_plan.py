import unittest

from portable.implementation_plan import WorkStep, ImplementationPlanner
from portable.requirement_contract import Requirement, RequirementContractEngine


class ImplementationPlanTests(unittest.TestCase):
    def test_parallel_groups_are_dependency_safe(self):
        plan = ImplementationPlanner().build([
            WorkStep("api", "API", verification=("integration",)),
            WorkStep("ui", "UI", verification=("browser",)),
            WorkStep("e2e", "E2E", ("api", "ui"), ("e2e",)),
        ])
        self.assertEqual(plan.parallel_groups[0], ("api", "ui"))
        self.assertEqual(plan.parallel_groups[1], ("e2e",))
        self.assertTrue(plan.complete)

    def test_cycle_rejected(self):
        with self.assertRaises(ValueError):
            ImplementationPlanner().build([
                WorkStep("a", "a", ("b",), ("t",)),
                WorkStep("b", "b", ("a",), ("t",)),
            ])

    def test_bound_plan_is_stale_when_requirement_contract_changes(self):
        engine = RequirementContractEngine()
        contract = engine.build(
            "build app",
            (
                Requirement("R1", "live data", ("fresh data",)),
                Requirement("R2", "news feed", ("source links",)),
            ),
        )
        planner = ImplementationPlanner()
        plan = planner.build(
            (
                WorkStep("quotes", "implement quotes", verification=("quote-test",), requirement_ids=("R1",)),
                WorkStep("news", "implement news", verification=("news-test",), requirement_ids=("R2",)),
            ),
            requirement_contract=contract,
        )
        revised = engine.revise_requirement(
            contract, "R1", Requirement("R1", "delayed data", ("age is shown",)),
        )
        invalidated = planner.invalidate_for_requirement_change(plan, revised, ("R1",))
        self.assertEqual(("quotes",), invalidated.stale_step_ids)
        self.assertFalse(invalidated.complete)
        with self.assertRaisesRegex(ValueError, "stale"):
            planner.validate_binding(plan, revised)
        with self.assertRaisesRegex(ValueError, "invalidated"):
            planner.validate_binding(invalidated, revised)

    def test_requirement_change_invalidates_transitive_dependents_only(self):
        engine = RequirementContractEngine()
        contract = engine.build(
            "build app",
            tuple(Requirement(f"R{i}", f"requirement {i}", (f"acceptance {i}",)) for i in range(1, 5)),
        )
        planner = ImplementationPlanner()
        plan = planner.build(
            (
                WorkStep("api", "API", verification=("api-test",), requirement_ids=("R1",)),
                WorkStep("ui", "UI", verification=("ui-test",), requirement_ids=("R2",)),
                WorkStep("e2e", "E2E", ("api", "ui"), ("e2e-test",), ("R3",)),
                WorkStep("docs", "Docs", verification=("docs-check",), requirement_ids=("R4",)),
            ),
            requirement_contract=contract,
        )
        revised = engine.revise_requirement(
            contract, "R1", Requirement("R1", "changed API", ("new API behavior",)),
        )
        invalidated = planner.invalidate_for_requirement_change(plan, revised, ("R1",))
        self.assertEqual(("api", "e2e"), invalidated.stale_step_ids)
        self.assertEqual("docs", next(step.step_id for step in invalidated.steps if step.step_id == "docs"))
        self.assertNotIn("ui", invalidated.stale_step_ids)


if __name__ == "__main__":
    unittest.main()
