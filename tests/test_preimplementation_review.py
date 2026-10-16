"""Pre-implementation review tests: constructive blind spots and conflict handling."""
import unittest
from portable.engineering_design_guard import EngineeringDesignGuard
from portable.orchestration import Graph, Node, NodeKind, Orchestrator, RunStatus


class TestPreImplementationReview(unittest.TestCase):
    def test_explicit_contradictions_include_collaborative_question_and_options(self):
        result = EngineeringDesignGuard.review_request(
            intent="Preserve backward compatibility while making a breaking change",
            requirements=["Preserve backward compatibility", "Introduce a breaking change"],
        )
        self.assertFalse(result["implementation_may_start"])
        self.assertEqual(result["unresolved_contradictions"][0]["id"], "compatibility-vs-breaking-change")
        self.assertTrue(result["collaborative_options"])
        self.assertIn("not silently choose", result["clarifying_questions"][0])

    def test_assumptions_and_vague_goals_surface_testable_questions(self):
        result = EngineeringDesignGuard.review_request(
            intent="Make the API fast, robust, and scalable",
            assumptions=["We assume the existing cache is always fresh"],
        )
        messages = " ".join(item["message"] for item in result["blind_spots"])
        self.assertIn("Performance is described qualitatively", messages)
        self.assertIn("Scalability is asserted", messages)
        self.assertIn("Unverified assumption", messages)
        self.assertTrue(any("latency" in question.lower() for question in result["clarifying_questions"]))

    def test_documented_resolution_unblocks_conflict_without_erasing_record(self):
        result = EngineeringDesignGuard.review_request(
            intent="Preserve backward compatibility while making a breaking change",
            resolutions={"compatibility-vs-breaking-change": "Ship v2 separately and keep v1 contract unchanged."},
        )
        self.assertTrue(result["implementation_may_start"])
        self.assertEqual(len(result["contradictions"]), 1)
        self.assertEqual(result["contradictions"][0]["status"], "resolved")
        self.assertIn("v1 contract unchanged", result["contradictions"][0]["resolution"])

    def test_digest_is_deterministic_and_binds_requirements(self):
        first = EngineeringDesignGuard.review_request(intent="Add bounded retries", requirements=["At most three attempts"])
        same = EngineeringDesignGuard.review_request(intent="Add bounded retries", requirements=["At most three attempts"])
        changed = EngineeringDesignGuard.review_request(intent="Add bounded retries", requirements=["At most four attempts"])
        self.assertEqual(first["digest"], same["digest"])
        self.assertNotEqual(first["digest"], changed["digest"])

    def test_unresolved_conflict_blocks_graph_before_any_node_runs(self):
        calls = []
        graph = Graph([Node("implement", NodeKind.DETERMINISTIC, lambda state: calls.append("ran") or {"ok": True})])
        run = Orchestrator(graph).run(
            "conflict-task", "Implement the requested API change",
            context={"requirements": ["Preserve backward compatibility", "Introduce a breaking change"]},
        )
        self.assertEqual(run.status, RunStatus.BLOCKED)
        self.assertEqual(calls, [])
        self.assertIn("contradictory requirements", run.stop_reason)
        evidence = next(item for item in run.evidence if item.kind == "preimplementation-review")
        self.assertIn("compatibility-vs-breaking-change", evidence.summary)

    def test_resolved_conflict_is_recorded_and_graph_can_run(self):
        calls = []
        graph = Graph([Node("implement", NodeKind.DETERMINISTIC, lambda state: calls.append("ran") or {"ok": True})])
        run = Orchestrator(graph).run(
            "resolved-conflict-task", "Implement the requested API change",
            context={
                "requirements": ["Preserve backward compatibility", "Introduce a breaking change"],
                "preimplementation_resolutions": {"compatibility-vs-breaking-change": "Ship v2 separately; keep v1 unchanged."},
            },
        )
        self.assertEqual(run.status, RunStatus.ACCEPTED, (run.stop_reason, run.trajectory))
        self.assertEqual(calls, ["ran"])
        review = next(item for item in run.trajectory if item.get("event") == "preimplementation_review")
        self.assertEqual(review["unresolved_contradiction_count"], 0)


if __name__ == "__main__":
    unittest.main()
