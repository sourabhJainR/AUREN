from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portable.subprocess_provider import SubprocessProviderInvoker, SubprocessProviderError


class SubprocessProviderInvokerTests(unittest.TestCase):
    def invoker(self, code: str, **kwargs) -> SubprocessProviderInvoker:
        return SubprocessProviderInvoker(
            [sys.executable, "-c", code],
            cwd=ROOT,
            **kwargs,
        )

    def test_round_trips_prompt_without_shell_or_inherited_environment(self):
        secret_name = "AUREN_SUBPROCESS_PROVIDER_TEST_SECRET"
        os.environ[secret_name] = "must-not-cross-boundary"
        try:
            provider = self.invoker(
                "import os,sys; print(os.environ.get('AUREN_SUBPROCESS_PROVIDER_TEST_SECRET', 'absent')); print(sys.stdin.read())"
            )
            result = provider('{"task":"safe"}', 3)
        finally:
            os.environ.pop(secret_name, None)
        self.assertTrue(result.startswith("absent\n"))
        self.assertIn('{"task":"safe"}', result)

    def test_timeout_terminates_non_cooperative_child(self):
        provider = self.invoker("import time; time.sleep(10)")
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "was terminated"):
            provider("request", 0.3)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_output_is_capped(self):
        provider = self.invoker("print('x' * 500)", max_response_bytes=100)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            provider("request", 2)

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
