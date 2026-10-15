"""Regression tests for bounded, responsive supervisor control loops."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.autonomous_daemon import PersistentSupervisorDaemon


class SupervisorLoopResponsivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.daemon = PersistentSupervisorDaemon(Path(self.temp.name), checkpoint_seconds=30)

    def test_finite_cycle_mode_returns_without_recursion(self):
        calls = []
        self.daemon.checkpoint = lambda: calls.append("tick")
        with patch("portable.autonomous_daemon.time.sleep"):
            self.daemon.run_forever(max_cycles=3, sleep_seconds=0.01)
        self.assertEqual(calls, ["tick", "tick", "tick"])

    def test_stop_is_polled_during_long_idle_interval(self):
        stopped = {"value": False}
        calls = []
        self.daemon.checkpoint = lambda: calls.append("tick")

        def sleep_and_signal(_seconds):
            stopped["value"] = True

        with patch("portable.autonomous_daemon.time.sleep", side_effect=sleep_and_signal) as sleeper:
            self.daemon.run_forever(stop=lambda: stopped["value"], sleep_seconds=300)
        self.assertEqual(calls, ["tick"])
        self.assertEqual(sleeper.call_count, 1)
        self.assertLessEqual(sleeper.call_args.args[0], 1.0)

    def test_repeated_checkpoint_failure_terminates_at_budget(self):
        calls = []

        def fail():
            calls.append("failure")
            raise OSError("temporary store unavailable")

        self.daemon.checkpoint = fail
        with patch("portable.autonomous_daemon.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "2 consecutive checkpoint failures"):
                self.daemon.run_forever(
                    sleep_seconds=0.01, max_consecutive_failures=2
                )
        self.assertEqual(len(calls), 2)

    def test_invalid_budgets_fail_fast(self):
        with self.assertRaisesRegex(ValueError, "max_tasks_per_tick"):
            PersistentSupervisorDaemon(Path(self.temp.name) / "other", max_tasks_per_tick=0)
        with self.assertRaisesRegex(ValueError, "max_cycles"):
            self.daemon.run_forever(max_cycles=0)
        with self.assertRaisesRegex(ValueError, "sleep_seconds"):
            self.daemon.run_forever(sleep_seconds=float("inf"))


if __name__ == "__main__":
    unittest.main()
