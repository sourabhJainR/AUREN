import unittest

from portable.agent_capabilities import CapabilityOption, CapabilityExecutioner
from portable.skill_set_evolution import AdaptiveSkillSetEvolver


class AdaptiveSkillSetEvolutionTests(unittest.TestCase):
    def test_proposes_bounded_remove_and_swap_mutations(self):
        options = (
            CapabilityOption("planner", tags=frozenset({"plan"}), source="core"),
            CapabilityOption("reviewer", tags=frozenset({"review"}), source="skill"),
            CapabilityOption("implementer", tags=frozenset({"implementation"}), source="mcp"),
        )
        history = {
            "planner": {"evidence_quality": 0.2, "success_rate": 0.7, "confidence": 0.5, "avg_cost": 0.1, "avg_latency": 0.2},
            "reviewer": {"evidence_quality": 0.9, "success_rate": 0.9, "confidence": 0.9, "avg_cost": 0.1, "avg_latency": 0.2},
            "implementer": {"evidence_quality": 0.95, "success_rate": 0.95, "confidence": 0.9, "avg_cost": 0.1, "avg_latency": 0.2},
        }
        contribution = {
            "planner": {"evidence_quality": 0.1},
            "reviewer": {"evidence_quality": 0.9},
            "implementer": {"evidence_quality": 0.95},
        }
        bundle = "parent"
        mutations = AdaptiveSkillSetEvolver(min_expected_delta=0.01, max_mutations=8).propose(
            options=options,
            bundle_history={bundle: {"members": ("planner", "reviewer"), "samples": 6, "collaboration_delta": 0.1}},
            history=history,
            contribution_history=contribution,
        )
        self.assertTrue(mutations)
        self.assertTrue(any(m.action == "remove" and m.members == ("reviewer",) for m in mutations))
        self.assertTrue(any(m.action == "swap" and "implementer" in m.members for m in mutations))
        self.assertLessEqual(len(mutations), 8)


    def test_mutation_respects_resource_budget(self):
        options = (
            CapabilityOption("planner", tags=frozenset({"plan"}), estimated_cost=0.3),
            CapabilityOption("reviewer", tags=frozenset({"review"}), estimated_cost=0.3),
            CapabilityOption("implementer", tags=frozenset({"implementation"}), estimated_cost=0.4),
        )
        mutations = AdaptiveSkillSetEvolver(min_expected_delta=0.0).propose(
            options=options,
            bundle_history={"parent": {"members": ("planner",), "samples": 4, "collaboration_delta": 0.1}},
            history={
                "planner": {"evidence_quality": 0.5, "success_rate": 0.5, "avg_cost": 0.3},
                "reviewer": {"evidence_quality": 0.9, "success_rate": 0.9, "avg_cost": 0.3},
                "implementer": {"evidence_quality": 0.9, "success_rate": 0.9, "avg_cost": 0.4},
            },
            contribution_history={},
            resource_budget=0.5,
        )
        self.assertFalse(any(mutation.action == "add" and "implementer" in mutation.members for mutation in mutations))

    def test_negative_parent_is_not_evolved(self):
        option = CapabilityOption("planner", tags=frozenset({"plan"}))
        mutations = AdaptiveSkillSetEvolver().propose(
            options=(option,),
            bundle_history={"bad": {"members": ("planner",), "samples": 4, "collaboration_delta": -0.2}},
            history={"planner": {"evidence_quality": 0.5}},
            contribution_history={},
        )
        self.assertEqual(mutations, ())

    def test_selector_surfaces_evolution_lineage(self):
        selector = CapabilityExecutioner(min_exploration=0.0)
        planner = CapabilityOption("planner", tags=frozenset({"plan"}), evidence_quality=0.7)
        reviewer = CapabilityOption("reviewer", tags=frozenset({"review"}), evidence_quality=0.9)
        result = selector.select_collaborative(
            request="plan review",
            options=(planner, reviewer),
            history={
                "planner": {"evidence_quality": 0.3, "success_rate": 0.7, "confidence": 0.6, "avg_cost": 0.1, "avg_latency": 0.2},
                "reviewer": {"evidence_quality": 0.9, "success_rate": 0.9, "confidence": 0.9, "avg_cost": 0.1, "avg_latency": 0.2},
            },
            contribution_history={
                "planner": {"evidence_quality": 0.1},
                "reviewer": {"evidence_quality": 0.9},
            },
            bundle_history={
                selector.bundle_id((planner, reviewer)): {
                    "members": ("planner", "reviewer"),
                    "samples": 6,
                    "success_rate": 0.8,
                    "failure_rate": 0.1,
                    "evidence_quality": 0.8,
                    "confidence": 0.8,
                    "collaboration_delta": 0.2,
                }
            },
            max_skills=2,
        )
        self.assertIn(result.evolution_action, {"baseline", "remove", "add", "swap"})
        self.assertTrue(result.evolution_candidates)
        self.assertIn(result.evolution_stage, {"none", "canary", "promoted"})


if __name__ == "__main__":
    unittest.main()
