from __future__ import annotations

import unittest

from portable.engineering_gate_crew_plan import (
    build_gate_crew_plan,
    dispatch_gate_crew_proposals,
    gate_definitions,
    gate_plan_digest,
    ready_gates,
    transition_gate,
)


class EngineeringGateCrewPlanTests(unittest.TestCase):
    def test_gate_dag_starts_only_with_post_merge_evidence(self):
        gates = gate_definitions()
        self.assertEqual(9, len(gates))
        self.assertEqual(("post_merge_evidence",), ready_gates())
        self.assertIn("runtime_isolation_reconciliation", {g.gate_id for g in gates})

    def test_crew_plans_use_canonical_crew_contracts_and_surface_missing_adapters(self):
        first = build_gate_crew_plan(
            graph_digest="graph-sha", intent_digest="intent-sha", agents={},
        )
        second = build_gate_crew_plan(
            graph_digest="graph-sha", intent_digest="intent-sha", agents={},
        )
        self.assertEqual(gate_plan_digest(first), gate_plan_digest(second))
        self.assertEqual(9, len(first))
        isolation = next(item for item in first if item.gate_id == "runtime_isolation_reconciliation")
        self.assertIn("security_privacy", isolation.required_crews)
        self.assertIn("security_privacy", isolation.unregistered_required_crews)
        self.assertTrue(all(item.crew_plan.assignments for item in first))

    def test_dependencies_must_pass_before_gate_can_start(self):
        with self.assertRaisesRegex(ValueError, "prerequisites"):
            transition_gate({}, "durable_plan_dispatch", "ready")

    def test_gate_pass_requires_evidence_and_external_trust_verification(self):
        statuses = {"post_merge_evidence": "passed", "durable_plan_dispatch": "passed"}
        statuses = transition_gate(statuses, "runtime_isolation_reconciliation", "ready")
        statuses = transition_gate(statuses, "runtime_isolation_reconciliation", "in_progress")
        with self.assertRaisesRegex(ValueError, "evidence references"):
            transition_gate(statuses, "runtime_isolation_reconciliation", "passed")
        with self.assertRaisesRegex(PermissionError, "out-of-band"):
            transition_gate(
                statuses, "runtime_isolation_reconciliation", "passed",
                evidence_refs=("operator://attestation/1",),
            )
        statuses = transition_gate(
            statuses, "runtime_isolation_reconciliation", "passed",
            evidence_refs=("operator://attestation/1",),
            external_evidence_verifier=lambda gate_id, refs: gate_id == "runtime_isolation_reconciliation" and refs == ("operator://attestation/1",),
        )
        self.assertEqual("passed", statuses["runtime_isolation_reconciliation"])

    def test_failed_gate_does_not_unlock_downstream_work(self):
        statuses = {"post_merge_evidence": "passed", "durable_plan_dispatch": "failed"}
        self.assertNotIn("runtime_isolation_reconciliation", ready_gates(statuses))
        with self.assertRaisesRegex(ValueError, "invalid gate transition"):
            transition_gate(statuses, "durable_plan_dispatch", "passed", evidence_refs=("ci://test",))


    def test_active_graph_dispatches_advisory_proposals_and_keeps_review_pending(self):
        from portable.state_graph import default_crew_definitions

        class ProposalAdapter:
            def __init__(self, definition):
                self.crew_id = definition.crew_id
                self.definition = definition
                self.configuration_digest = "test-config:" + definition.crew_id
                self.implementation_version = "test-v1"

            def propose(self, request):
                return {
                    "schema_version": "auren-crew-proposal-v1",
                    "recommendation": "Review gate " + request["evidence"]["gate_id"],
                    "artifacts": list(request["required_artifacts"]),
                    "evidence_refs": [],
                    "uncertainty": ["Independent review remains pending."],
                    "blockers": [],
                    "acceptance_check_results": {
                        name: {"status": "not_run", "evidence_refs": []}
                        for name in self.definition.acceptance_checks
                    },
                }

        agents = {item.crew_id: ProposalAdapter(item) for item in default_crew_definitions()}
        work_items = build_gate_crew_plan(graph_digest="graph", intent_digest="intent", agents=agents)
        item = next(value for value in work_items if value.gate_id == "post_merge_evidence")
        proposals = dispatch_gate_crew_proposals(
            gate_id=item.gate_id, work_item=item, agents=agents, active=True,
            evidence={"source_revision": "exact-main-sha"},
        )
        self.assertEqual(set(item.required_crews), set(proposals))
        self.assertTrue(all(value["review_required"] for value in proposals.values()))
        self.assertTrue(all(value["review_status"] == "pending" for value in proposals.values()))
        self.assertTrue(all(value["self_attested_acceptance_is_not_authoritative"] for value in proposals.values()))

    def test_gate_dispatch_rejects_inactive_graph_and_missing_crew_before_dispatch(self):
        work_items = build_gate_crew_plan(graph_digest="graph", intent_digest="intent", agents={})
        item = next(value for value in work_items if value.gate_id == "post_merge_evidence")
        with self.assertRaisesRegex(RuntimeError, "active graph execution"):
            dispatch_gate_crew_proposals(
                gate_id=item.gate_id, work_item=item, agents={}, active=False,
            )
        with self.assertRaisesRegex(PermissionError, "not registered"):
            dispatch_gate_crew_proposals(
                gate_id=item.gate_id, work_item=item, agents={}, active=True,
            )

    def test_crew_plan_identity_changes_with_intent(self):
        first = build_gate_crew_plan(graph_digest="graph", intent_digest="intent-a", agents={})
        second = build_gate_crew_plan(graph_digest="graph", intent_digest="intent-b", agents={})
        self.assertNotEqual(gate_plan_digest(first), gate_plan_digest(second))


if __name__ == "__main__":
    unittest.main()
