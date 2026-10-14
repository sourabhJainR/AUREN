from __future__ import annotations

import json
import unittest

from portable.orchestration import Graph, Node, NodeKind, NodeStatus, Orchestrator, RunStatus


class IndependentGraphProgressTests(unittest.TestCase):
    def test_critical_failure_blocks_acceptance_but_independent_branches_still_run(self):
        calls = []

        def fail(_state):
            calls.append("critical")
            raise RuntimeError("critical branch failed")

        def dependent(_state):
            calls.append("dependent")
            return "must not run"

        def independent(_state):
            calls.append("independent")
            return "independent work completed"

        graph = Graph([
            Node("a-critical", NodeKind.DETERMINISTIC, fail, critical=True),
            Node("b-dependent", NodeKind.DETERMINISTIC, dependent, depends_on=("a-critical",), critical=False),
            Node("z-independent", NodeKind.DETERMINISTIC, independent, critical=False),
        ])
        result = Orchestrator(graph).run(
            task_id="independent-progress",
            intent="Continue independent work when a dependency fails",
            context={"risk": "low"},
        )

        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertIn("critical branch failed", result.stop_reason or "")
        self.assertEqual(calls, ["critical", "independent"])
        self.assertEqual(result.results["b-dependent"].status, NodeStatus.SKIPPED)
        progress = next(item for item in result.evidence if item.kind == "independent-graph-progress")
        payload = json.loads(progress.summary)
        self.assertEqual(payload["status"], "blocked")
        self.assertIn("z-independent", payload["executed_nodes"])
        self.assertIn("b-dependent", payload["skipped_nodes"])


if __name__ == "__main__":
    unittest.main()
