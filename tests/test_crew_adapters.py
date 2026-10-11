from __future__ import annotations

import unittest

from portable.crew_adapters import (
    ApprovedTool,
    ApprovedToolCrewAdapter,
    ApprovedToolRegistry,
    GraphHandoffController,
    rollback_readiness,
)


def proposal(request):
    return {
        "schema_version": "auren-crew-proposal-v1",
        "recommendation": "review required",
        "artifacts": list(request["required_artifacts"]),
        "evidence_refs": [],
        "uncertainty": ["Independent review is pending."],
        "blockers": [],
        "acceptance_check_results": {
            name: {"status": "not_run", "evidence_refs": []}
            for name in request["acceptance_checks"]
        },
    }


class ApprovedCrewAdapterTests(unittest.TestCase):
    def test_adapter_invokes_only_explicit_read_only_tools(self):
        calls = []
        registry = ApprovedToolRegistry([
            ApprovedTool("inspect_diff", lambda args: calls.append(dict(args)) or {"files": 2}, "Read-only diff inspection"),
            ApprovedTool("search_docs", lambda args: {"matches": []}, "Read-only documentation search"),
        ])
        adapter = ApprovedToolCrewAdapter(
            "qa_reviewer",
            lambda request: {
                "tool_calls": [{"name": "inspect_diff", "arguments": {"path": "src/"}}],
                "proposal": proposal(request),
            },
            registry,
            allowed_tools=("inspect_diff",),
        )
        request = {
            "crew_id": "qa_reviewer", "task": "Review change",
            "required_artifacts": ["review findings"], "acceptance_checks": ["tests are tied to requirements"],
            "secret": "must not cross boundary",
        }
        result = adapter.propose(request)
        self.assertEqual(result["schema_version"], "auren-crew-proposal-v1")
        self.assertEqual(calls, [{"path": "src/"}])

    def test_adapter_rejects_unapproved_tool_and_side_effecting_registry(self):
        registry = ApprovedToolRegistry([ApprovedTool("read", lambda _args: {}, "Read-only")])
        adapter = ApprovedToolCrewAdapter(
            "python", lambda request: {
                "tool_calls": [{"name": "write_file", "arguments": {}}],
                "proposal": proposal(request),
            }, registry, allowed_tools=("read",),
        )
        with self.assertRaisesRegex(PermissionError, "not approved"):
            adapter.propose({
                "crew_id": "python", "required_artifacts": [], "acceptance_checks": [],
            })
        with self.assertRaisesRegex(ValueError, "side-effecting"):
            ApprovedToolRegistry([ApprovedTool("merge", lambda _args: {}, "Mutation", side_effecting=True)])

    def test_tool_count_and_output_are_bounded(self):
        registry = ApprovedToolRegistry([ApprovedTool("read", lambda _args: object(), "Read-only")])
        adapter = ApprovedToolCrewAdapter(
            "python", lambda request: {
                "tool_calls": [{"name": "read", "arguments": {}}],
                "proposal": proposal(request),
            }, registry, allowed_tools=("read",), max_tool_calls=1,
        )
        with self.assertRaisesRegex(ValueError, "JSON-serializable"):
            adapter.propose({"crew_id": "python", "required_artifacts": [], "acceptance_checks": []})
        with self.assertRaisesRegex(ValueError, "max_tool_calls"):
            ApprovedToolCrewAdapter("python", lambda _r: {}, registry, allowed_tools=("read",), max_tool_calls=99)

    def test_handoffs_require_contract_and_graph_assignment(self):
        controller = GraphHandoffController(max_attempts=2)
        with self.assertRaisesRegex(PermissionError, "not declared"):
            controller.enqueue(
                source_crew="frontend_ui", target_crew="security_privacy", task="Review auth",
                source_plan_digest="abc", allowed_handoffs=("qa_reviewer",),
                selected_crews=("frontend_ui", "security_privacy"),
            )
        with self.assertRaisesRegex(PermissionError, "not assigned"):
            controller.enqueue(
                source_crew="frontend_ui", target_crew="qa_reviewer", task="Review UI",
                source_plan_digest="abc", allowed_handoffs=("qa_reviewer",),
                selected_crews=("frontend_ui",),
            )
        handoff = controller.enqueue(
            source_crew="frontend_ui", target_crew="qa_reviewer", task="Review UI",
            source_plan_digest="abc", allowed_handoffs=("qa_reviewer",),
            selected_crews=("frontend_ui", "qa_reviewer"),
        )
        with self.assertRaisesRegex(RuntimeError, "active graph node"):
            controller.claim_next(graph_node_active=False)
        claimed = controller.claim_next(graph_node_active=True)
        self.assertEqual(claimed, handoff)
        retry = controller.retry(claimed, retryable=True)
        self.assertEqual(retry.attempt, 1)
        self.assertIsNone(controller.retry(retry, retryable=True))
        with self.assertRaisesRegex(PermissionError, "independent review"):
            controller.mark_completed(handoff, independent_review_passed=False)
        controller.mark_completed(handoff, independent_review_passed=True)

    def test_handoff_checkpoint_resumes_queued_work(self):
        controller = GraphHandoffController(max_attempts=3)
        controller.enqueue(
            source_crew="backend_api", target_crew="qa_reviewer", task="Review endpoint",
            source_plan_digest="plan-digest", allowed_handoffs=("qa_reviewer",),
            selected_crews=("backend_api", "qa_reviewer"),
        )
        resumed = GraphHandoffController.resume(controller.snapshot())
        claimed = resumed.claim_next(graph_node_active=True)
        self.assertEqual(claimed.target_crew, "qa_reviewer")
        self.assertEqual(claimed.task, "Review endpoint")

    def test_rollback_readiness_fails_closed(self):
        blocked = rollback_readiness(
            consequential=True, last_known_good_digest=None, rollback_procedure_ref=None,
            independent_review_passed=False, required_evidence_refs=("test://ci", "attest://run"),
            verified_evidence_refs=("test://ci",),
        )
        self.assertFalse(blocked["ready"])
        self.assertEqual(blocked["missing_evidence_refs"], ["attest://run"])
        ready = rollback_readiness(
            consequential=True, last_known_good_digest="sha256:known-good",
            rollback_procedure_ref="runbook://rollback", independent_review_passed=True,
            required_evidence_refs=("test://ci",), verified_evidence_refs=("test://ci",),
        )
        self.assertTrue(ready["ready"])


if __name__ == "__main__":
    unittest.main()
