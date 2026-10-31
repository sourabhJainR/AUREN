import unittest

from portable.orchestration import Graph, Node, NodeKind, NodeStatus, Orchestrator, RunStatus


class PartialDependencyExecutionTests(unittest.TestCase):
    def test_optional_failure_is_explicit_and_evaluator_gated(self):
        observed = {}

        def synthesize(state):
            observed["partial"] = list(state.get("auren_partial_dependencies", []))
            return {"accepted": observed["partial"] == [{
                "node": "optional_news", "status": "failed", "error_type": "RuntimeError",
            }]}

        graph = Graph([
            Node("optional_news", NodeKind.DETERMINISTIC,
                 lambda _state: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
                 critical=False),
            Node("required_fundamentals", NodeKind.DETERMINISTIC, lambda _state: {"price": 10}),
            Node("synthesis", NodeKind.AGENT, synthesize,
                 depends_on=("optional_news", "required_fundamentals"),
                 evaluator=lambda output: output.get("accepted") is True,
                 allow_partial_dependencies=True),
        ])
        result = Orchestrator(graph, allow_self_improvement=False).run(
            "partial-optional-source", "synthesize from available evidence"
        )
        self.assertEqual(result.results["optional_news"].status, NodeStatus.FAILED)
        self.assertEqual(result.results["synthesis"].status, NodeStatus.PASSED)
        self.assertEqual(result.results["synthesis"].partial_dependencies, ("optional_news",))
        self.assertEqual(observed["partial"][0]["node"], "optional_news")
        self.assertTrue(any(x.kind == "partial-dependencies-accepted"
                            for x in result.results["synthesis"].evidence))

    def test_critical_failure_blocks_partial_synthesis(self):
        called = []
        graph = Graph([
            Node("critical_fundamentals", NodeKind.DETERMINISTIC,
                 lambda _state: (_ for _ in ()).throw(RuntimeError("fundamentals unavailable")),
                 critical=True),
            Node("optional_news", NodeKind.DETERMINISTIC, lambda _state: {"stories": []}, critical=False),
            Node("synthesis", NodeKind.AGENT,
                 lambda _state: called.append("ran") or {"accepted": True},
                 depends_on=("critical_fundamentals", "optional_news"),
                 evaluator=lambda output: output.get("accepted") is True,
                 allow_partial_dependencies=True),
        ])
        result = Orchestrator(graph, allow_self_improvement=False).run(
            "partial-critical-source", "synthesize only with required fundamentals"
        )
        self.assertEqual(result.results["synthesis"].status, NodeStatus.SKIPPED)
        self.assertEqual(called, [])
        self.assertEqual(result.status, RunStatus.BLOCKED)

    def test_blocked_or_skipped_dependency_cannot_be_accepted_as_partial(self):
        called = []
        graph = Graph([
            Node("critical_root", NodeKind.DETERMINISTIC,
                 lambda _state: (_ for _ in ()).throw(RuntimeError("root unavailable")),
                 critical=True),
            Node("blocked_optional", NodeKind.DETERMINISTIC, lambda _state: {"unexpected": True},
                 depends_on=("critical_root",), critical=False),
            Node("synthesis", NodeKind.AGENT,
                 lambda _state: called.append("ran") or {"accepted": True},
                 depends_on=("blocked_optional",),
                 evaluator=lambda output: output.get("accepted") is True,
                 allow_partial_dependencies=True),
        ])
        result = Orchestrator(graph, allow_self_improvement=False).run(
            "partial-blocked-source", "blocked dependencies must remain blocking"
        )
        self.assertEqual(result.results["blocked_optional"].status, NodeStatus.SKIPPED)
        self.assertEqual(result.results["synthesis"].status, NodeStatus.SKIPPED)
        self.assertEqual(called, [])

    def test_partial_dependency_context_does_not_leak_to_unrelated_node(self):
        later_context = []
        graph = Graph([
            Node("optional_news", NodeKind.DETERMINISTIC,
                 lambda _state: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
                 critical=False),
            Node("required_fundamentals", NodeKind.DETERMINISTIC, lambda _state: {"price": 10}),
            Node("synthesis", NodeKind.AGENT,
                 lambda state: {"partial_count": len(state["auren_partial_dependencies"])},
                 depends_on=("optional_news", "required_fundamentals"),
                 evaluator=lambda output: output["partial_count"] == 1,
                 allow_partial_dependencies=True),
            Node("unrelated_followup", NodeKind.DETERMINISTIC,
                 lambda state: later_context.extend(state["auren_partial_dependencies"]) or {"ok": True},
                 depends_on=("synthesis",)),
        ])
        result = Orchestrator(graph, allow_self_improvement=False).run(
            "partial-context-isolation", "ensure partial state is invocation scoped"
        )
        self.assertEqual(result.results["synthesis"].status, NodeStatus.PASSED)
        self.assertEqual(result.results["unrelated_followup"].status, NodeStatus.PASSED)
        self.assertEqual(later_context, [])

    def test_partial_dependency_policy_requires_evaluator_and_changes_digest(self):
        with self.assertRaisesRegex(ValueError, "must define an evaluator"):
            Graph([Node("synthesis", NodeKind.AGENT, lambda _state: {},
                        allow_partial_dependencies=True)])
        strict = Graph([Node("synthesis", NodeKind.AGENT, lambda _state: {})])
        partial = Graph([Node("synthesis", NodeKind.AGENT, lambda _state: {},
                              evaluator=lambda _output: True,
                              allow_partial_dependencies=True)])
        self.assertNotEqual(strict.digest(), partial.digest())


if __name__ == "__main__":
    unittest.main()
