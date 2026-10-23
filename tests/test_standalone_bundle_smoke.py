"""Clean-install smoke test for the published standalone AUREN bundle."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

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


    def test_bundle_excludes_sqlite_state_and_transient_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            for relative in (
                ".ai-harness/runtime",
                "skills/ai-coding-orchestrator",
                "portable",
                "dashboard",
                ".claude-plugin",
            ):
                (root / relative).mkdir(parents=True, exist_ok=True)
            (root / ".ai-harness/config.toml").write_text('[harness]\nversion = 1\n', encoding="utf-8")
            (root / ".claude-plugin/plugin.json").write_text('{"version":"1.0.0"}\n', encoding="utf-8")
            (root / "skills/ai-coding-orchestrator/SKILL.md").write_text("portable skill\n", encoding="utf-8")
            (root / "portable/auren_runtime.py").write_text("portable runtime\n", encoding="utf-8")
            (root / "dashboard/index.html").write_text("<html>dashboard</html>\n", encoding="utf-8")
            (root / "auren_cli.py").write_text("launcher\n", encoding="utf-8")
            (root / "auren_env_compat.py").write_text(
                "def get_auren_env(suffix, default=None):\n    return default\n",
                encoding="utf-8",
            )

            state = root / ".ai-harness" / "learning"
            state.mkdir(parents=True, exist_ok=True)
            names = (
                "task-memory.sqlite",
                "task-memory.sqlite3",
                "task-memory.sqlite3-shm",
                "task-memory.sqlite3-wal",
                "task-memory.sqlite3-journal",
                "cache.db",
                "cache.db3",
                "cache.db3-wal",
                "cache.db3-shm",
                "cache.db3-journal",
                "notes-shm",
            )
            for name in names:
                (state / name).write_bytes(b"mutable runtime state")

            bundle = Path(tmp) / "auren.zip"
            build(root, bundle, source_commit="portable-state-test")
            manifest = verify_bundle(bundle)
            included = {item["path"] for item in manifest["files"]}
            with zipfile.ZipFile(bundle) as archive:
                archived = set(archive.namelist())
            for name in names:
                relative = f".ai-harness/learning/{name}"
                if name == "notes-shm":
                    self.assertIn(relative, included)
                    self.assertIn(f"payload/{relative}", archived)
                else:
                    self.assertNotIn(relative, included)
                    self.assertNotIn(f"payload/{relative}", archived)


if __name__ == "__main__":
    unittest.main()
