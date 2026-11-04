import json
import unittest

from portable.engineering_design_guard import EngineeringDesignGuard
from portable.engineering_requirement_intake import parse_engineering_request
from portable.orchestration import Graph, Node, NodeKind, Orchestrator, RunStatus


class EngineeringRequirementIntakeTests(unittest.TestCase):
    def test_extracts_candidates_with_source_offsets_and_stable_digest(self):
        intent = (
            "Build a stock research application.\n"
            "Assume provider credentials are configured.\n"
            "Do not expose provider secrets.\n"
            "Out of scope: user accounts.\n"
            "The API must return source timestamps."
        )
        first = parse_engineering_request(intent)
        second = parse_engineering_request(intent)

        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(first["source_digest"], second["source_digest"])
        self.assertTrue(first["complete"])
        self.assertFalse(first["requirements_verified"])
        self.assertTrue(first["semantic_review_required"])
        self.assertIn("Build a stock research application.", first["requirements"])
        self.assertIn("Assume provider credentials are configured.", first["assumptions"])
        self.assertIn("Do not expose provider secrets.", first["constraints"])
        self.assertIn("Out of scope: user accounts.", first["non_goals"])
        self.assertIn("The API must return source timestamps.", first["acceptance_criteria"])
        for statement in first["statements"]:
            self.assertEqual(
                intent[statement["start"]:statement["end"]],
                statement["text"],
            )

    def test_inline_credentials_are_redacted_from_intake_evidence(self):
        intent = "Configure API key=sk-live-example and password=hunter2."
        result = parse_engineering_request(intent)
        serialized = json.dumps(result, sort_keys=True)

        self.assertNotIn("sk-live-example", serialized)
        self.assertNotIn("hunter2", serialized)
        self.assertEqual(result["redacted_statement_count"], 1)
        self.assertEqual(result["redacted_value_count"], 2)
        statement = result["statements"][0]
        self.assertTrue(statement["redacted"])
        self.assertIsNone(statement["statement_digest"])
        self.assertIn("[REDACTED]", statement["text"])

    def test_sentence_boundaries_preserve_abbreviations_and_decimal_values(self):
        result = parse_engineering_request(
            "Build a service e.g. a FastAPI endpoint. Keep 99.9% availability."
        )
        self.assertEqual(
            [item["text"] for item in result["statements"]],
            [
                "Build a service e.g. a FastAPI endpoint.",
                "Keep 99.9% availability.",
            ],
        )

    def test_oversized_statement_is_incomplete_not_silently_truncated(self):
        intent = "Build an application. " + ("requirement " * 80)
        result = parse_engineering_request(intent)
        self.assertFalse(result["complete"])
        self.assertTrue(result["oversized_statement_spans"])

    def test_stock_research_material_conflicts_are_detected_from_plain_language(self):
        cases = (
            (
                "Build live market quotes using a free provider with 15-minute delayed quotes.",
                "real-time-vs-delayed-market-data",
            ),
            (
                "Allow public redistribution of provider data. The provider license prohibits redistribution.",
                "public-redistribution-vs-license",
            ),
            (
                "The MVP must have no user accounts. Provide persistent per-user watchlists.",
                "no-accounts-vs-per-user-state",
            ),
            (
                "Generate fully automated recommendations. Require human approval before publishing each recommendation.",
                "automated-recommendation-vs-human-approval",
            ),
            (
                "Do not store external market data. Provide durable historical price analytics.",
                "no-external-storage-vs-historical-analytics",
            ),
            (
                "Deliver a quick MVP. Include broad market coverage and high availability.",
                "fast-mvp-vs-broad-operational-scope",
            ),
        )
        for intent, conflict_id in cases:
            with self.subTest(conflict_id=conflict_id):
                result = EngineeringDesignGuard.review_request(intent=intent)
                self.assertFalse(result["implementation_may_start"])
                self.assertIn(
                    conflict_id,
                    {item["id"] for item in result["unresolved_contradictions"]},
                )

    def test_orchestrator_blocks_plain_language_conflict_before_any_node_runs(self):
        calls = []
        graph = Graph([
            Node(
                "build_stock_app",
                NodeKind.DETERMINISTIC,
                lambda state: calls.append("ran") or {"ok": True},
            )
        ])
        intent = (
            "Build live market quotes. "
            "Use a free provider with 15-minute delayed quotes. "
            "Show the result as real-time."
        )
        run = Orchestrator(graph).run("stock-intake-conflict", intent)

        self.assertEqual(run.status, RunStatus.BLOCKED)
        self.assertEqual(calls, [])
        review = next(item for item in run.evidence if item.kind == "preimplementation-review")
        review_payload = json.loads(review.summary)
        conflict = next(
            item for item in review_payload["conflict_details"]
            if item["id"] == "real-time-vs-delayed-market-data"
        )
        self.assertEqual(
            conflict["requirement_a"]["statement"],
            "Build live market quotes.",
        )
        self.assertEqual(
            conflict["requirement_b"]["statement"],
            "Use a free provider with 15-minute delayed quotes.",
        )
        intake = next(
            item for item in run.evidence
            if item.kind == "engineering-requirement-intake"
        )
        self.assertIn("candidate_extraction", intake.summary)

    def test_review_evidence_uses_sanitized_intent_and_binds_original_digest(self):
        secret = "sk-test-super-secret-value"
        intent = f"Build live quotes. Delayed quotes. API key={secret}."
        graph = Graph([
            Node("implement", NodeKind.DETERMINISTIC, lambda state: {"ok": True})
        ])
        run = Orchestrator(graph).run("redacted-review", intent)
        self.assertEqual(run.status, RunStatus.BLOCKED)
        evidence_text = "\\n".join(item.summary for item in run.evidence)

        self.assertNotIn(secret, evidence_text)
        intake = next(
            item for item in run.evidence
            if item.kind == "engineering-requirement-intake"
        )
        review = next(
            item for item in run.evidence
            if item.kind == "preimplementation-review"
        )
        intake_payload = json.loads(intake.summary)
        review_payload = json.loads(review.summary)
        self.assertEqual(review_payload["source_intent_digest"], intake_payload["source_digest"])
        self.assertEqual(review_payload["intake_digest"], intake_payload["digest"])
        self.assertIn("[REDACTED]", review.summary)

    def test_incomplete_intake_blocks_before_graph_side_effects(self):
        calls = []
        graph = Graph([
            Node("implement", NodeKind.DETERMINISTIC, lambda state: calls.append("ran"))
        ])
        intent = "Implement " + ("a very long requirement " * 50)
        run = Orchestrator(graph).run("oversized-intake", intent)
        self.assertEqual(run.status, RunStatus.BLOCKED)
        self.assertEqual(calls, [])
        self.assertIn("intake exceeded safe extraction bounds", run.stop_reason)


if __name__ == "__main__":
    unittest.main()
