from __future__ import annotations

import unittest

from portable.engineering_gate_dispatch import build_gate_engineering_plan


class EngineeringGateDispatchPlanTests(unittest.TestCase):
    def test_gate_dag_compiles_to_canonical_plan_with_only_roots_ready(self):
        plan, run = build_gate_engineering_plan(
            run_id="gate-run-1", source_revision="a" * 40,
            graph_digest="graph-sha", intent_digest="intent-sha", agents={},
        )
        statuses = {item.task_id: item.status for item in run.tasks}
        self.assertEqual(9, len(plan.tasks))
        self.assertEqual("ready", statuses["post_merge_evidence"])
        self.assertTrue(all(
            status == "pending"
            for task_id, status in statuses.items()
            if task_id != "post_merge_evidence"
        ))
        durable = next(task for task in plan.tasks if task.task_id == "runtime_isolation_reconciliation")
        self.assertIn("durable_plan_dispatch", durable.depends_on)
        self.assertEqual("critical", durable.risk)
        self.assertTrue(any("Gate crew-plan digest:" in item for item in plan.requirements))

    def test_gate_plan_is_deterministic_for_identical_inputs(self):
        kwargs = dict(
            run_id="gate-run-2", source_revision="b" * 40,
            graph_digest="graph-sha", intent_digest="intent-sha", agents={},
        )
        first, first_run = build_gate_engineering_plan(**kwargs)
        second, second_run = build_gate_engineering_plan(**kwargs)
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first_run.digest, second_run.digest)

    def test_plan_preserves_dependencies_as_authoritative_task_edges(self):
        plan, _ = build_gate_engineering_plan(
            run_id="gate-run-3", source_revision="c" * 40,
            graph_digest="graph-sha", intent_digest="intent-sha", agents={},
        )
        by_id = {task.task_id: task for task in plan.tasks}
        self.assertEqual(
            ("runtime_isolation_reconciliation", "provenance_learning", "safe_parallel_execution"),
            by_id["guarded_promotion"].depends_on,
        )
        self.assertIn("operator-approved rollback procedure", by_id["guarded_promotion"].rollback_plan)


if __name__ == "__main__":
    unittest.main()
