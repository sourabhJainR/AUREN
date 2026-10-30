import unittest

from portable.orchestration import (
    Graph,
    Node,
    NodeKind,
    NodeStatus,
    Orchestrator,
    RunStatus,
)


class PartialDependencyTests(unittest.TestCase):
    def test_evaluator_approved_join_runs_with_failed_noncritical_provider(self):
        def fail_news(_state):
            raise TimeoutError("credential=must-not-leak")

        def synthesize(state):
            return {
                "partial": bool(state["auren_partial_dependencies"]),
                "dependencies": state["auren_partial_dependencies"],
                "fundamentals": state["fundamentals"],
            }

        graph = Graph([
            Node("fundamentals", NodeKind.DETERMINISTIC, lambda _state: {"price": 10}),
            Node("news", NodeKind.DETERMINISTIC, fail_news, critical=False),
            Node(
                "synthesis", NodeKind.JOIN, synthesize,
                depends_on=("fundamentals", "news"),
                evaluator=lambda output: (
                    output["partial"]
                    and output["fundamentals"]["price"] == 10
                    and output["dependencies"][0]["node"] == "news"
                    and output["dependencies"][0]["error_type"] == "TimeoutError"
                    and "credential" not in str(output)
                ),
                allow_partial_dependencies=True,
            ),
        ])

        orchestrator = Orchestrator(graph)
        run = orchestrator.run("partial-stock-brief", "build evidence-first stock brief")

        self.assertEqual(run.status, RunStatus.ACCEPTED)
        result = run.results["synthesis"]
        self.assertEqual(result.status, NodeStatus.PASSED)
        self.assertEqual(result.partial_dependencies, ("news",))
        self.assertTrue(any(
            event.get("event") == "partial_dependencies_accepted"
            and event.get("dependencies") == ["news"]
            for event in run.trajectory
        ))
        replay = orchestrator.replay(run)
        self.assertEqual(replay["nodes"]["synthesis"]["partial_dependencies"], ["news"])
        self.assertTrue(any(item.kind == "partial-dependencies-accepted" for item in result.evidence))

    def test_critical_failure_cannot_be_accepted_as_partial(self):
        called = []

        def fail_critical(_state):
            raise RuntimeError("required source failed")

        graph = Graph([
            Node("required_source", NodeKind.DETERMINISTIC, fail_critical, critical=True),
            Node(
                "synthesis", NodeKind.JOIN,
                lambda _state: called.append(True) or {"accepted": True},
                depends_on=("required_source",),
                evaluator=lambda output: output.get("accepted") is True,
                allow_partial_dependencies=True,
            ),
        ])
        run = Orchestrator(graph).run("critical-source-failure", "build stock brief")
        self.assertEqual(run.results["synthesis"].status, NodeStatus.SKIPPED)
        self.assertEqual(called, [])
        self.assertNotEqual(run.status, RunStatus.ACCEPTED)

    def test_skipped_dependency_cannot_be_bypassed_by_partial_policy(self):
        graph = Graph([
            Node(
                "root", NodeKind.DETERMINISTIC,
                lambda _state: (_ for _ in ()).throw(RuntimeError("root failed")),
                critical=False,
            ),
            Node(
                "dependent", NodeKind.DETERMINISTIC, lambda _state: {"unexpected": True},
                depends_on=("root",), critical=False,
            ),
            Node(
                "synthesis", NodeKind.JOIN, lambda _state: {"unexpected": True},
                depends_on=("dependent",),
                evaluator=lambda output: bool(output),
                allow_partial_dependencies=True,
            ),
        ])
        run = Orchestrator(graph).run("skipped-dependency", "build stock brief")
        self.assertEqual(run.results["dependent"].status, NodeStatus.SKIPPED)
        self.assertEqual(run.results["synthesis"].status, NodeStatus.SKIPPED)
        self.assertNotEqual(run.status, RunStatus.ACCEPTED)

    def test_partial_dependency_policy_requires_an_evaluator(self):
        with self.assertRaisesRegex(ValueError, "must define an evaluator"):
            Graph([
                Node(
                    "synthesis", NodeKind.JOIN, lambda _state: {"ok": True},
                    allow_partial_dependencies=True,
                )
            ])

    def test_partial_dependency_policy_is_bound_into_graph_digest(self):
        strict = Graph([
            Node("source", NodeKind.DETERMINISTIC, lambda _state: "ok"),
            Node(
                "join", NodeKind.JOIN, lambda _state: {"ok": True},
                depends_on=("source",), evaluator=lambda output: bool(output),
            ),
        ])
        partial = Graph([
            Node("source", NodeKind.DETERMINISTIC, lambda _state: "ok"),
            Node(
                "join", NodeKind.JOIN, lambda _state: {"ok": True},
                depends_on=("source",), evaluator=lambda output: bool(output),
                allow_partial_dependencies=True,
            ),
        ])
        self.assertNotEqual(strict.digest(), partial.digest())


    def test_partial_synthesis_evaluator_rejection_fails_critical_join(self):
        graph = Graph([
            Node("required", NodeKind.DETERMINISTIC, lambda _state: {"ok": True}),
            Node(
                "optional", NodeKind.DETERMINISTIC,
                lambda _state: (_ for _ in ()).throw(TimeoutError("optional source down")),
                critical=False,
            ),
            Node(
                "synthesis", NodeKind.JOIN,
                lambda state: {"partial": bool(state["auren_partial_dependencies"])},
                depends_on=("required", "optional"),
                evaluator=lambda _output: False,
                allow_partial_dependencies=True,
            ),
        ])
        run = Orchestrator(graph).run("partial-evaluator-reject", "build stock brief")
        self.assertEqual(run.results["synthesis"].status, NodeStatus.FAILED)
        self.assertEqual(run.status, RunStatus.FAILED)



if __name__ == "__main__":
    unittest.main()
