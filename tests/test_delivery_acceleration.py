from __future__ import annotations

import unittest

from portable.delivery_acceleration import analyze_delivery_parallelism
from portable.engineering_plan import EngineeringPlan, PlanTask


def plan(*tasks: PlanTask) -> EngineeringPlan:
    return EngineeringPlan(
        run_id="acceleration-run", goal="Reduce delivery lead time",
        requirements=("independent work runs concurrently",),
        source_revision="test-revision", tasks=tasks,
    )


class DeliveryAccelerationTests(unittest.TestCase):
    def test_independent_work_is_parallel_and_speedup_is_measured(self):
        value = plan(
            PlanTask("api", "API", ("contract passes",), "revert"),
            PlanTask("ui", "UI", ("ui tests pass",), "revert"),
            PlanTask("qa", "QA", ("suite passes",), "revert", depends_on=("api", "ui")),
        )
        result = analyze_delivery_parallelism(
            value, durations_seconds={"api": 4, "ui": 6, "qa": 2},
            worker_limit=2, coordination_overhead_seconds=0.5,
            serial_task_ids=("qa",),
        )
        by_id = {task.task_id: task for task in result.tasks}
        self.assertEqual(0, by_id["api"].start_seconds)
        self.assertEqual(0, by_id["ui"].start_seconds)
        self.assertEqual(6, by_id["qa"].start_seconds)
        self.assertEqual(8.5, result.scheduled_seconds)
        self.assertEqual(12, result.sequential_seconds)
        self.assertEqual(0.5, result.coordination_seconds)
        self.assertAlmostEqual(12 / 8.5, result.scheduled_speedup)
        self.assertLessEqual(result.scheduled_speedup, result.amdahl_upper_bound)
        self.assertLessEqual(result.scheduled_speedup, result.critical_path_speedup_bound)

    def test_dependency_chain_remains_serial_under_more_workers(self):
        value = plan(
            PlanTask("a", "A", ("done",), "revert"),
            PlanTask("b", "B", ("done",), "revert", depends_on=("a",)),
            PlanTask("c", "C", ("done",), "revert", depends_on=("b",)),
        )
        result = analyze_delivery_parallelism(
            value, durations_seconds={"a": 2, "b": 3, "c": 5}, worker_limit=8,
            serial_task_ids=("a", "b", "c"),
        )
        self.assertEqual(10, result.scheduled_seconds)
        self.assertEqual(1.0, result.serial_fraction)
        self.assertEqual(1.0, result.amdahl_upper_bound)
        self.assertEqual(1.0, result.critical_path_speedup_bound)
        self.assertEqual(1.0, result.scheduled_speedup)

    def test_shared_resource_lock_serializes_independent_tasks(self):
        value = plan(
            PlanTask("one", "One", ("done",), "revert"),
            PlanTask("two", "Two", ("done",), "revert"),
        )
        result = analyze_delivery_parallelism(
            value, durations_seconds={"one": 3, "two": 4}, worker_limit=2,
            resource_locks={"one": ("shared-db",), "two": ("shared-db",)},
            serial_task_ids=(),
        )
        by_id = {task.task_id: task for task in result.tasks}
        self.assertEqual(0, by_id["one"].start_seconds)
        self.assertEqual(3, by_id["two"].start_seconds)
        self.assertEqual(7, result.scheduled_seconds)

    def test_resource_independent_tasks_use_multiple_worker_slots(self):
        value = plan(
            PlanTask("one", "One", ("done",), "revert"),
            PlanTask("two", "Two", ("done",), "revert"),
        )
        result = analyze_delivery_parallelism(
            value, durations_seconds={"one": 3, "two": 4}, worker_limit=2,
            serial_task_ids=(),
        )
        self.assertEqual({0, 1}, {task.worker_slot for task in result.tasks})
        self.assertEqual(4, result.scheduled_seconds)

    def test_duration_and_worker_inputs_fail_closed(self):
        value = plan(PlanTask("one", "One", ("done",), "revert"))
        with self.assertRaisesRegex(ValueError, "exactly match"):
            analyze_delivery_parallelism(value, durations_seconds={}, worker_limit=1, serial_task_ids=())
        with self.assertRaisesRegex(ValueError, "positive"):
            analyze_delivery_parallelism(value, durations_seconds={"one": 0}, worker_limit=1, serial_task_ids=())
        with self.assertRaisesRegex(ValueError, "worker_limit"):
            analyze_delivery_parallelism(value, durations_seconds={"one": 1}, worker_limit=0, serial_task_ids=())

    def test_declared_serial_work_is_serialized_in_the_schedule(self):
        value = plan(
            PlanTask("one", "One", ("done",), "revert"),
            PlanTask("two", "Two", ("done",), "revert"),
        )
        result = analyze_delivery_parallelism(
            value, durations_seconds={"one": 3, "two": 4}, worker_limit=4,
            serial_task_ids=("one", "two"),
        )
        by_id = {task.task_id: task for task in result.tasks}
        self.assertEqual(0, by_id["one"].start_seconds)
        self.assertEqual(3, by_id["two"].start_seconds)
        self.assertEqual(7, result.scheduled_seconds)
        self.assertEqual(1.0, result.serial_fraction)
        self.assertEqual(1.0, result.amdahl_upper_bound)

    def test_amdahl_estimate_requires_explicit_serial_task_classification(self):
        value = plan(PlanTask("one", "One", ("done",), "revert"))
        with self.assertRaisesRegex(ValueError, "explicitly supplied"):
            analyze_delivery_parallelism(
                value, durations_seconds={"one": 1}, worker_limit=2,
            )
        classified = analyze_delivery_parallelism(
            value, durations_seconds={"one": 1}, worker_limit=2,
            serial_task_ids=(),
        )
        self.assertEqual(0.0, classified.serial_fraction)
        self.assertEqual(2.0, classified.amdahl_upper_bound)

    def test_unknown_resource_lock_owner_is_rejected(self):
        value = plan(PlanTask("one", "One", ("done",), "revert"))
        with self.assertRaisesRegex(ValueError, "unknown tasks"):
            analyze_delivery_parallelism(
                value, durations_seconds={"one": 1}, worker_limit=1,
                resource_locks={"missing": ("db",)},
                serial_task_ids=(),
            )


if __name__ == "__main__":
    unittest.main()
