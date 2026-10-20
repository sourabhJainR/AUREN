from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

import engine


def deadline(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class HarnessTaskDeadlineTests(unittest.TestCase):
    def test_deadline_remaining_seconds_requires_timezone_and_reports_expiry(self):
        self.assertGreater(engine.deadline_remaining_seconds(deadline(30)), 0)
        self.assertLess(engine.deadline_remaining_seconds(deadline(-1)), 0)
        with self.assertRaisesRegex(ValueError, "timezone"):
            engine.deadline_remaining_seconds("2030-01-01T00:00:00")
        with self.assertRaisesRegex(ValueError, "ISO-8601"):
            engine.deadline_remaining_seconds("not-a-date")

    def test_validation_does_not_start_commands_after_task_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "validation": {
                    "commands": [[sys.executable, "-c", "raise SystemExit(99)"]],
                    "auto_discover": False,
                },
                "execution": {"validation_timeout_seconds": 60},
            }
            passed, results = engine.run_validation(
                config, Path(tmp), deadline_at=deadline(-1),
            )
            self.assertFalse(passed)
            self.assertEqual(results[0]["status"], "deadline_exceeded")
            self.assertEqual(results[0]["exit_code"], 124)

    def test_validation_timeout_terminates_hung_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "validation": {"commands": [[sys.executable, "-c", "import time; time.sleep(30)"]], "auto_discover": False},
                "execution": {"validation_timeout_seconds": 0.2, "validation_max_output_bytes": 1024},
            }
            start = __import__("time").monotonic()
            passed, results = engine.run_validation(config, Path(tmp))
            self.assertFalse(passed)
            self.assertEqual("timeout", results[0]["status"])
            self.assertLess(__import__("time").monotonic() - start, 3.0)

    def test_parent_deadline_is_propagated_to_validation_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "validation": {"commands": [[sys.executable, "-c", "import time; time.sleep(30)"]], "auto_discover": False},
                "execution": {"validation_timeout_seconds": 10, "validation_max_output_bytes": 1024},
            }
            passed, results = engine.run_validation(config, Path(tmp), deadline_at=deadline(0.2))
            self.assertFalse(passed)
            self.assertEqual("deadline_exceeded", results[0]["status"])
            self.assertEqual("parent_deadline_exceeded", results[0]["termination_reason"])

    def test_validation_output_capture_is_bounded_and_reports_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            config = {
                "validation": {"commands": [[sys.executable, "-c", "print('x' * 200000)"]], "auto_discover": False},
                "execution": {"validation_timeout_seconds": 3, "validation_max_output_bytes": 512},
            }
            passed, results = engine.run_validation(config, run_dir)
            self.assertTrue(passed)
            self.assertTrue(results[0]["stdout_truncated"])
            self.assertLess((run_dir / "validation.log").stat().st_size, 2000)
            self.assertIn("output truncated", (run_dir / "validation.log").read_text(encoding="utf-8"))

    def test_pre_cancelled_validation_does_not_start_command(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            cancel = threading.Event()
            cancel.set()
            config = {
                "validation": {"commands": [[sys.executable, "-c", "raise SystemExit(99)"]], "auto_discover": False},
                "execution": {"validation_timeout_seconds": 3},
            }
            passed, results = engine.run_validation(config, Path(tmp), cancellation_event=cancel)
            self.assertFalse(passed)
            self.assertEqual("cancelled", results[0]["status"])
            self.assertEqual(130, results[0]["exit_code"])

    def test_task_deadline_setting_is_bounded_in_config(self):
        import tomllib

        config = tomllib.loads((ROOT / ".ai-harness" / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["execution"]["task_deadline_seconds"], 3600)


if __name__ == "__main__":
    unittest.main()
