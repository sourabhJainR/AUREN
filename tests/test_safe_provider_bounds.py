from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

from safe_provider import _bounded_process_output, _run_resilient


class SafeProviderBoundTests(unittest.TestCase):
    def run_command(self, code: str, **kwargs):
        return _bounded_process_output(
            [sys.executable, "-c", code],
            cwd=ROOT, env=dict(os.environ), output_sink=kwargs.pop("output_sink", lambda _chunk: None),
            **kwargs,
        )

    def test_output_is_streamed_and_returned_within_limit(self):
        seen = []
        code, output = self.run_command(
            "print('bounded-output')", timeout_seconds=2, max_output_bytes=1024, output_sink=seen.append,
        )
        self.assertEqual(0, code)
        self.assertEqual("bounded-output\n", output)
        self.assertEqual(b"bounded-output\n", b"".join(seen))

    def test_output_overflow_terminates_process_and_fails_closed(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.run_command(
                "import sys,time; sys.stdout.write('x' * 1000000); sys.stdout.flush(); time.sleep(10)",
                timeout_seconds=2, max_output_bytes=1024,
            )
        self.assertLess(time.monotonic() - started, 2)

    def test_deadline_terminates_non_cooperative_process(self):
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "deadline"):
            self.run_command("import time; time.sleep(10)", timeout_seconds=0.2, max_output_bytes=1024)
        self.assertLess(time.monotonic() - started, 2)

    def test_parent_exit_does_not_leave_descendant_running(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "descendant-survived.txt"
            child_code = (
                "import pathlib,sys,time; time.sleep(0.7); "
                "pathlib.Path(sys.argv[1]).write_text('survived', encoding='utf-8')"
            )
            parent_code = (
                "import subprocess,sys; subprocess.Popen([sys.executable, '-c', "
                + repr(child_code) + ", " + repr(str(marker))
                + "], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
                "print('parent-complete')"
            )
            code, output = self.run_command(parent_code, timeout_seconds=3, max_output_bytes=1024)
            self.assertEqual(0, code)
            self.assertIn("parent-complete", output)
            time.sleep(0.9)
            self.assertFalse(marker.exists(), "provider descendants must not outlive a completed invocation")

    def test_invalid_process_limits_return_125_without_launching_provider(self):
        class RecordingFeedback:
            def __init__(self):
                self.events = []

            def observe(self, **event):
                self.events.append(event)

        cases = (
            ("HARNESS_PROVIDER_TIMEOUT_SECONDS", "not-a-number"),
            ("HARNESS_PROVIDER_MAX_OUTPUT_BYTES", "not-a-number"),
        )
        for name, value in cases:
            with self.subTest(setting=name), tempfile.TemporaryDirectory() as directory:
                feedback = RecordingFeedback()

                def configured_value(key, default=None):
                    return value if key == name else default

                with patch("safe_provider.get_auren_env", side_effect=configured_value), \
                     patch("safe_provider._bounded_process_output") as invoke:
                    code = _run_resilient(
                        [sys.executable, "-c", "print('must-not-run')"],
                        "bounded provider configuration regression",
                        Path(directory),
                        feedback,
                    )

                self.assertEqual(125, code)
                invoke.assert_not_called()
                self.assertEqual("output_limit_or_invalid_config", feedback.events[-1]["outcome"])


if __name__ == "__main__":
    unittest.main()
