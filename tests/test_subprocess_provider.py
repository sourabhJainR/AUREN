from __future__ import annotations

import math
import os
import sys
import tempfile
import time
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portable.subprocess_provider import SubprocessProviderInvoker, SubprocessProviderError


class SubprocessProviderInvokerTests(unittest.TestCase):
    def invoker(self, code: str, **kwargs) -> SubprocessProviderInvoker:
        return SubprocessProviderInvoker([sys.executable, "-c", code], cwd=ROOT, **kwargs)

    def test_round_trips_prompt_without_shell_or_inherited_environment(self):
        name = "AUREN_SUBPROCESS_PROVIDER_TEST_SECRET"
        previous = os.environ.get(name)
        os.environ[name] = "must-not-cross-boundary"
        try:
            provider = self.invoker("import os,sys; print(os.environ.get('AUREN_SUBPROCESS_PROVIDER_TEST_SECRET', 'absent')); print(sys.stdin.read())")
            result = provider('{"task":"safe"}', 3)
        finally:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self.assertEqual("absent", result.splitlines()[0])
        self.assertIn('{"task":"safe"}', result)


    def test_absolute_supervisor_deadline_is_propagated_and_capped(self):
        now = time.time()
        inherited = now + 1.5
        provider = self.invoker(
            "import os; print(os.environ['AUREN_DEADLINE_EPOCH'])",
            env={"AUREN_DEADLINE_EPOCH": str(inherited)},
        )
        result = provider("request", 5)
        propagated = float(result.strip())
        self.assertGreater(propagated, now)
        self.assertLessEqual(propagated, inherited)
        self.assertAlmostEqual(inherited, propagated, delta=0.01)

        provider = self.invoker("import os; print(os.environ['AUREN_DEADLINE_EPOCH'])")
        started = time.time()
        propagated = float(provider("request", 2).strip())
        self.assertGreaterEqual(propagated, started + 1.8)
        self.assertLessEqual(propagated, started + 2.1)

    def test_expired_or_invalid_supervisor_deadline_prevents_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "provider-launched.txt"
            command = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('launched')"
            expired = self.invoker(command, env={"AUREN_DEADLINE_EPOCH": str(time.time() - 1)})
            with self.assertRaisesRegex(TimeoutError, "expired before provider launch"):
                expired("request", 5)
            self.assertFalse(marker.exists())

            invalid = self.invoker(command, env={"AUREN_DEADLINE_EPOCH": "not-a-timestamp"})
            with self.assertRaisesRegex(ValueError, "finite Unix timestamp"):
                invalid("request", 5)
            self.assertFalse(marker.exists())

    def test_non_finite_and_excessive_timeouts_are_rejected_before_launch(self):
        provider = self.invoker("raise SystemExit('must not launch')")
        for timeout in (float("nan"), float("inf"), 86401):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "finite and between"):
                provider("request", timeout)

    def test_timeout_terminates_non_cooperative_child(self):
        provider = self.invoker("import time; time.sleep(10)")
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "was terminated"):
            provider("request", 0.3)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_pre_cancelled_call_does_not_launch(self):
        provider = self.invoker("raise SystemExit('must not launch')")
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(InterruptedError, "before launch"):
            provider("request", 2, cancel_event=cancelled)

    def test_cancellation_kills_non_cooperative_child(self):
        provider = self.invoker("import time; time.sleep(10)")
        cancelled = threading.Event()
        trigger = threading.Thread(target=lambda: (time.sleep(0.1), cancelled.set()), daemon=True)
        trigger.start()
        started = time.monotonic()
        with self.assertRaisesRegex(InterruptedError, "cancelled and terminated"):
            provider("request", 5, cancel_event=cancelled)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_output_limit_is_enforced_while_reading(self):
        provider = self.invoker("import sys; sys.stdout.write('x' * 1000000); sys.stdout.flush()", max_response_bytes=100)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            provider("request", 2)

    def test_prompt_limit_is_enforced(self):
        provider = self.invoker("import sys; print(sys.stdin.read())", max_prompt_bytes=10)
        with self.assertRaisesRegex(ValueError, "prompt exceeds"):
            provider("x" * 11, 2)

    def test_nonzero_exit_does_not_expose_stderr(self):
        provider = self.invoker("import sys; print('private diagnostic', file=sys.stderr); sys.exit(7)")
        with self.assertRaisesRegex(SubprocessProviderError, "code 7") as caught:
            provider("request", 2)
        self.assertNotIn("private diagnostic", str(caught.exception))


    @unittest.skipUnless(os.name in {"nt", "posix"}, "process-group containment is platform-specific")
    def test_successful_parent_exit_does_not_leave_provider_descendant(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "descendant-survived.txt"
            child_code = (
                "import pathlib,sys,time; time.sleep(1.0); "
                "pathlib.Path(sys.argv[1]).write_text('survived', encoding='utf-8')"
            )
            parent_code = (
                "import subprocess,sys; "
                "subprocess.Popen([sys.executable, '-c', "
                + repr(child_code)
                + ", " + repr(str(marker)) + "], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                "stderr=subprocess.DEVNULL); print('parent-complete')"
            )
            provider = self.invoker(parent_code)
            result = provider("request", 3)
            self.assertIn("parent-complete", result)
            time.sleep(1.3)
            self.assertFalse(
                marker.exists(),
                "provider descendants must not outlive a completed invocation",
            )



    def test_provider_descendants_are_killed_on_timeout_cancel_and_output_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "descendant-survived.txt"
            child_code = (
                "import pathlib,sys,time; time.sleep(1.0); "
                "pathlib.Path(sys.argv[1]).write_text('survived', encoding='utf-8')"
            )
            parent_prefix = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable, '-c', "
                + repr(child_code) + ", " + repr(str(marker))
                + "], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
            )
            scenarios = (
                ("timeout", parent_prefix + "time.sleep(10)", {}, 0.25, TimeoutError),
                ("cancel", parent_prefix + "time.sleep(10)", {}, 3, InterruptedError),
                (
                    "overflow",
                    parent_prefix + "sys.stdout.write('x' * 1000000); sys.stdout.flush(); time.sleep(10)",
                    {"max_response_bytes": 100},
                    3,
                    ValueError,
                ),
            )
            for name, code, options, timeout, expected in scenarios:
                with self.subTest(exit_path=name):
                    marker.unlink(missing_ok=True)
                    provider = self.invoker(code, **options)
                    cancelled = threading.Event()
                    trigger = None
                    if name == "cancel":
                        trigger = threading.Thread(
                            target=lambda: (time.sleep(0.15), cancelled.set()),
                            daemon=True,
                        )
                        trigger.start()
                    with self.assertRaises(expected):
                        provider("request", timeout, cancel_event=cancelled if name == "cancel" else None)
                    if trigger is not None:
                        trigger.join(timeout=1)
                    time.sleep(1.2)
                    self.assertFalse(
                        marker.exists(),
                        f"provider descendant survived the {name} exit path",
                    )

    def test_rejects_relative_executable_and_invalid_environment(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            SubprocessProviderInvoker(["python", "-c", "pass"], cwd=ROOT)
        with self.assertRaisesRegex(ValueError, "env"):
            self.invoker("pass", env={"BAD=KEY": "value"})


if __name__ == "__main__":
    unittest.main()
