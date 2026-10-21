"""Clean-install smoke test for the published standalone AUREN bundle."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from portable.auren_runtime import build, install, verify_bundle


ROOT = Path(__file__).resolve().parents[1]


class StandaloneBundleSmokeTests(unittest.TestCase):
    def test_installed_cli_runs_outside_checkout_without_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            bundle = temp / "auren-portable.zip"
            home = temp / "isolated-home"
            workspace = temp / "unrelated-workspace"
            workspace.mkdir()
            build(ROOT, bundle, source_commit="standalone-smoke-test")
            manifest = verify_bundle(bundle)
            self.assertEqual("standalone-smoke-test", manifest["source_commit"])
            install(bundle, "none", home)

            installed_cli = home / "current" / "auren_cli.py"
            self.assertTrue(installed_cli.is_file())
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            env["HOME"] = str(temp / "user-home")
            env["USERPROFILE"] = str(temp / "user-home")
            result = subprocess.run(
                [sys.executable, str(installed_cli), "--help"],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr[-2000:])
            self.assertIn("usage:", result.stdout.lower())
            self.assertNotIn(str(ROOT), result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
