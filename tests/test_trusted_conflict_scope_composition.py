from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.adaptive_runtime import AdaptiveRuntime
from portable.durable_approval import DurableApprovalStore
from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
from portable.engineering_plan_store import EngineeringPlanStore
from portable.orchestration import Graph, Node, NodeKind, NodeStatus, RunStatus


class TrustedConflictScopeCompositionTests(unittest.TestCase):
    def _runtime(self, root: Path, graph: Graph, verifier=None) -> AdaptiveRuntime:
        runtime = AdaptiveRuntime(graph, state_root=root)
        store = EngineeringPlanStore(root / "engineering-plans.sqlite3")
        recovery_bridge = EngineeringPlanRecoveryBridge(
            store=store,
            ledger=runtime.recovery_ledger,
        )
        approval_store = DurableApprovalStore(runtime.execution_controller)
        runtime.configure_trusted_plan_dispatch(
            store=store,
            recovery_bridge=recovery_bridge,
            approval_store=approval_store,
            budget_resolver=lambda *_args: {},
            approval_resolver=lambda *_args: None,
            assignment_resolver=lambda _context: None,
            conflict_scope_verifier=verifier,
        )
        return runtime

    @staticmethod
    def _conflict_context():
        return {
            "requirements": [
                "Preserve backward compatibility",
                "Introduce a breaking change",
            ],
            "preimplementation_conflict_nodes": {
                "compatibility-vs-breaking-change": ["api"],
            },
        }

    def test_host_composition_root_verifier_allows_only_reviewed_independent_branch(self):
        calls = []
        seen = []
        graph = Graph([
            Node("api", NodeKind.DETERMINISTIC, lambda _state: calls.append("api") or "api"),
            Node("docs", NodeKind.DETERMINISTIC, lambda _state: calls.append("docs") or "docs"),
        ])

        def verify_conflict_scope(review, mapping, graph_digest):
            seen.append((review, mapping, graph_digest))
            return (
                bool(review.get("unresolved_contradictions"))
                and mapping == {"compatibility-vs-breaking-change": ("api",)}
                and len(graph_digest) == 64
            )

        with tempfile.TemporaryDirectory() as directory:
            runtime = self._runtime(Path(directory), graph, verify_conflict_scope)
            self.assertIs(runtime.orchestrator.conflict_scope_verifier, verify_conflict_scope)
            run = runtime.orchestrator.run(
                "trusted-root-conflict",
                "Preserve compatibility while changing the API",
                context=self._conflict_context(),
            )

        self.assertEqual(run.status, RunStatus.BLOCKED)
        self.assertEqual(calls, ["docs"])
        self.assertEqual(run.results["api"].status, NodeStatus.BLOCKED)
        self.assertEqual(run.results["docs"].status, NodeStatus.PASSED)
        self.assertEqual(len(seen), 1)

    def test_missing_host_verifier_keeps_conflict_admission_fail_closed(self):
        calls = []
        graph = Graph([
            Node("api", NodeKind.DETERMINISTIC, lambda _state: calls.append("api")),
            Node("docs", NodeKind.DETERMINISTIC, lambda _state: calls.append("docs")),
        ])

        with tempfile.TemporaryDirectory() as directory:
            runtime = self._runtime(Path(directory), graph)
            self.assertIsNone(runtime.orchestrator.conflict_scope_verifier)
            run = runtime.orchestrator.run(
                "missing-host-verifier",
                "Preserve compatibility while changing the API",
                context=self._conflict_context(),
            )

        self.assertEqual(run.status, RunStatus.BLOCKED)
        self.assertEqual(calls, [])
        admission = next(
            event for event in run.trajectory
            if event.get("event") == "conflict_scoped_admission"
        )
        self.assertEqual(admission["status"], "blocked")

    def test_conflict_verifier_cannot_be_installed_after_graph_execution_starts(self):
        graph = Graph([
            Node("work", NodeKind.DETERMINISTIC, lambda _state: "ok"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = AdaptiveRuntime(graph, state_root=root)
            runtime.orchestrator.run("already-started", "Run the safe task")
            store = EngineeringPlanStore(root / "engineering-plans.sqlite3")
            recovery_bridge = EngineeringPlanRecoveryBridge(
                store=store,
                ledger=runtime.recovery_ledger,
            )
            approval_store = DurableApprovalStore(runtime.execution_controller)

            with self.assertRaisesRegex(RuntimeError, "before graph execution"):
                runtime.configure_trusted_plan_dispatch(
                    store=store,
                    recovery_bridge=recovery_bridge,
                    approval_store=approval_store,
                    budget_resolver=lambda *_args: {},
                    approval_resolver=lambda *_args: None,
                    assignment_resolver=lambda _context: None,
                    conflict_scope_verifier=lambda _review, _mapping, _digest: True,
                )


if __name__ == "__main__":
    unittest.main()
