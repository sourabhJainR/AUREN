from pathlib import Path
from portable.autonomous_company import AutonomousCompany

def test_ci_wait_records_pass_after_pending(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("ci gate")
    states = iter([("pending", "running", ("ci-1",)), ("passed", "green", ("ci-2",))])
    assert company.wait_for_ci(unit.id, lambda: next(states), poll_seconds=0.01, timeout_seconds=1) == "passed"
    score = company.trust(unit.id)
    assert score.components["ci"] == 1.0


import tempfile
import unittest
from unittest.mock import patch


class CiWaitLoopBoundsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.company = AutonomousCompany(Path(self.temp.name))

    def test_ci_wait_rejects_invalid_time_budgets(self):
        unit = self.company.start("invalid ci budget")
        with self.assertRaisesRegex(ValueError, "poll_seconds"):
            self.company.wait_for_ci(unit.id, lambda: ("pending", "", ()), poll_seconds=0)
        with self.assertRaisesRegex(ValueError, "timeout_seconds"):
            self.company.wait_for_ci(unit.id, lambda: ("pending", "", ()), timeout_seconds=float("inf"))
        with self.assertRaisesRegex(ValueError, "max_polls"):
            self.company.wait_for_ci(unit.id, lambda: ("pending", "", ()), max_polls=0)

    def test_ci_wait_poll_budget_terminates_pending_provider(self):
        unit = self.company.start("bounded ci polls")
        calls = []
        result = self.company.wait_for_ci(
            unit.id,
            lambda: calls.append(1) or ("pending", "still running", ()),
            poll_seconds=0.001,
            timeout_seconds=2,
            max_polls=2,
        )
        self.assertEqual(result, "poll_limit")
        self.assertEqual(len(calls), 2)

    def test_ci_wait_can_be_cancelled_responsively_during_sleep(self):
        import portable.autonomous_company as module

        unit = self.company.start("cancel ci wait")
        stopped = {"value": False}
        calls = []

        def fake_sleep(_seconds):
            stopped["value"] = True

        with patch.object(module.time, "sleep", side_effect=fake_sleep):
            result = self.company.wait_for_ci(
                unit.id,
                lambda: calls.append(1) or ("pending", "still running", ()),
                poll_seconds=30,
                timeout_seconds=60,
                stop=lambda: stopped["value"],
            )
        self.assertEqual(result, "cancelled")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
