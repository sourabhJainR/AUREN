"""Tests for dependency-aware parallel Jira batch planning."""
from __future__ import annotations

import unittest

from portable.jira_batch_planner import JiraBatchPlanner, JiraPlanError, JiraWorkItem


def item(
    key: str,
    *,
    depends_on: tuple[str, ...] = (),
    paths: tuple[str, ...] = (),
) -> JiraWorkItem:
    return JiraWorkItem(
        key=key,
        acceptance_criteria=("Observable acceptance condition",),
        depends_on=depends_on,
        likely_paths=paths,
    )


class JiraBatchPlannerTests(unittest.TestCase):
    def test_readies_only_items_whose_dependencies_are_complete(self) -> None:
        planner = JiraBatchPlanner([
            item("APP-1"),
            item("APP-2", depends_on=("APP-1",)),
            item("APP-3"),
        ])
        self.assertEqual(("APP-1", "APP-3"), planner.ready_items())
        self.assertEqual(("APP-2",), planner.ready_items(("APP-1", "APP-3")))
        wave = planner.next_wave()
        self.assertEqual({"APP-2": ("APP-1",)}, wave.blocked)

    def test_detects_cycles_and_missing_dependencies(self) -> None:
        with self.assertRaisesRegex(JiraPlanError, "cycle"):
            JiraBatchPlanner([
                item("APP-1", depends_on=("APP-2",)),
                item("APP-2", depends_on=("APP-1",)),
            ])
        with self.assertRaisesRegex(JiraPlanError, "missing dependencies"):
            JiraBatchPlanner([item("APP-1", depends_on=("APP-404",))])

    def test_shared_file_ownership_serializes_overlapping_items(self) -> None:
        planner = JiraBatchPlanner([
            item("APP-1", paths=("portable/shared.py", "tests/a.py")),
            item("APP-2", paths=("portable/shared.py", "tests/b.py")),
            item("APP-3", paths=("portable/other.py",)),
        ])
        self.assertEqual(
            {"portable/shared.py": ("APP-1", "APP-2")},
            planner.shared_path_conflicts(),
        )
        groups = planner.parallel_groups()
        self.assertTrue(any(set(group) == {"APP-1", "APP-3"} for group in groups))
        self.assertTrue(any(set(group) == {"APP-2"} for group in groups))

    def test_unknown_path_estimates_are_isolated(self) -> None:
        planner = JiraBatchPlanner([item("APP-1"), item("APP-2", paths=("src/x.py",))])
        self.assertEqual((("APP-1",), ("APP-2",)), planner.parallel_groups())

    def test_normalizes_keys_and_rejects_duplicate_keys(self) -> None:
        planner = JiraBatchPlanner([item("app-1")])
        self.assertEqual(("APP-1",), planner.ready_items())
        with self.assertRaisesRegex(JiraPlanError, "duplicate"):
            JiraBatchPlanner([item("APP-1"), item("app-1")])

    def test_rejects_unknown_completed_items(self) -> None:
        planner = JiraBatchPlanner([item("APP-1")])
        with self.assertRaisesRegex(JiraPlanError, "not in this batch"):
            planner.ready_items(("APP-404",))


if __name__ == "__main__":
    unittest.main()
