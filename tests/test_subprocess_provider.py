from __future__ import annotations

import os
import sys
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
        self.assertTrue(result.startswith("absent\n"))
        self.assertIn('{"task":"safe"}', result)

    def test_timeout_terminates_non_cooperative_child(self):
        provider = self.invoker("import time; time.sleep(10)")
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "was terminated"):
            provider("request", 0.3)
        self.assertLess(time.monotonic() - started, 2.0)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group behavior")
    def test_orphaned_descendant_cannot_hold_stdout_open(self):
        code = (
            "import os, time; "
            "pid = os.fork(); "
            "os._exit(0) if pid == 0 and False else None; "
            "time.sleep(10) if pid == 0 else os._exit(0)"
        )
        provider = self.invoker(code)
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "output stream did not close"):
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

    def test_rejects_relative_executable_and_invalid_environment(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            SubprocessProviderInvoker(["python", "-c", "pass"], cwd=ROOT)
        with self.assertRaisesRegex(ValueError, "env"):
            self.invoker("pass", env={"BAD=KEY": "value"})


if __name__ == "__main__":
    unittest.main()
