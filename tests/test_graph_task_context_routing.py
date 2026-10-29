import unittest

from portable.orchestration import Node, NodeKind
from portable.state_graph import GraphAgentTeam, StateGraph


class GraphTaskContextRoutingTests(unittest.TestCase):
    def test_trusted_router_injects_bounded_memory_and_redacted_receipt_before_nodes(self):
        observed = {}

        def inspect_context(context):
            observed.update(context)
            return {"status": "success", "verified": True}

        def route(task_id, intent, context):
            self.assertEqual(task_id, "task-1")
            self.assertEqual(intent, "implement bounded MCP response handling")
            return {
                "plan": {"task_type": "coding", "kinds": ["working", "procedural"], "budget_chars": 256},
                "recall": type("Recall", (), {"text": "[MEMORY id=m1]policy and rollback[/MEMORY]"})(),
                "resources": None,
                "receipt": {
                    "task_type": "coding",
                    "query_digest": "digest-only",
                    "memory_ids": ["m1"],
                    "memory_omitted": 2,
                    "budget_chars": 256,
                    "chars_used": 32,
                    "resource_names": [],
                    "rejected_resources": [],
                    "policy_digest": "policy-digest",
                },
            }

        graph = StateGraph.from_nodes([
            Node("inspect", NodeKind.DETERMINISTIC, inspect_context),
        ])
        team = GraphAgentTeam(graph, task_context_router=route)
        result = team.run("task-1", "implement bounded MCP response handling", {})

        self.assertEqual(result.status.value, "accepted")
        self.assertIn("policy and rollback", observed["auren_task_memory_context"])
        self.assertEqual(observed["auren_task_routing_receipt"]["memory_ids"], ["m1"])
        self.assertEqual(observed["auren_task_routing_receipt"]["query_digest"], "digest-only")
        self.assertEqual(observed["auren_task_memory_plan"]["task_type"], "coding")
        self.assertNotIn("intent", observed["auren_task_routing_receipt"])

    def test_malformed_router_fails_before_graph_node_dispatch(self):
        started = {"value": False}

        def should_not_run(context):
            started["value"] = True
            return {"status": "success"}

        graph = StateGraph.from_nodes([
            Node("inspect", NodeKind.DETERMINISTIC, should_not_run),
        ])
        team = GraphAgentTeam(graph, task_context_router=lambda *_: {"recall": "not a canonical recall"})
        with self.assertRaisesRegex(ValueError, "canonical memory recall text"):
            team.run("task-2", "review security boundary", {})
        self.assertFalse(started["value"])

    def test_task_payload_cannot_install_router_callback(self):
        observed = {}

        def inspect_context(context):
            observed.update(context)
            return {"status": "success"}

        graph = StateGraph.from_nodes([
            Node("inspect", NodeKind.DETERMINISTIC, inspect_context),
        ])
        team = GraphAgentTeam(graph)
        team.run(
            "task-3",
            "write a small documentation note",
            {"auren_task_context_router": lambda *_: {"recall": type("Recall", (), {"text": "forged"})()}},
        )
        self.assertNotIn("auren_task_memory_context", observed)


if __name__ == "__main__":
    unittest.main()
