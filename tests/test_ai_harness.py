import json
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / ".ai-harness" / "run.py"


class AdaptiveHarnessTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(RUNNER), *args], cwd=ROOT, text=True, capture_output=True, check=False)

    def test_capabilities(self) -> None:
        result = self.run_cli("capabilities")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.split()), {"research", "poc", "grill"})

    def test_runtime_policy_is_single_run(self) -> None:
        config = (ROOT / ".ai-harness" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('mode = "single-adaptive-run"', config)
        self.assertIn("looping_enabled = false", config)
        self.assertIn("recursive_self_invocation = false", config)

    def test_dry_run_creates_checkpoint_and_manifest(self) -> None:
        result = self.run_cli("run", "--task", "Add input validation", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        run_dirs = [path for path in (ROOT / ".ai-harness" / "runs").glob("*") if path.is_dir() and (path / "manifest.json").is_file()]
        self.assertTrue(run_dirs)
        run_dir = max(run_dirs, key=lambda path: path.stat().st_mtime)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], 7)
        self.assertEqual(manifest["workflow"], "adaptive")
        self.assertIn("route", manifest)
        self.assertIn("task_chunks", manifest)
        self.assertTrue((run_dir / "checkpoint.json").exists())
        self.assertTrue((run_dir / "execution-checkpoint.json").exists())
        self.assertTrue((run_dir / "repository-map.md").exists())
        self.assertTrue((run_dir / "execute.prompt.md").exists())

    def test_explicit_workflow_is_honored(self) -> None:
        result = self.run_cli("run", "--workflow", "research", "--task", "Compare two approaches", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        run_dirs = [path for path in (ROOT / ".ai-harness" / "runs").glob("*") if path.is_dir()]
        run_dir = max(run_dirs, key=lambda path: path.stat().st_mtime)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["workflow"], "research")
        self.assertEqual(manifest["phases"], ["route", "context", "research", "learn"])

    def test_eval_regression_suite(self) -> None:
        result = self.run_cli("eval")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertGreaterEqual(payload["cases"], 7)
        self.assertEqual(payload["failed"], 0, payload)

    def test_memory_and_groom_commands(self) -> None:
        self.assertEqual(self.run_cli("memory").returncode, 0)
        result = self.run_cli("groom")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("patterns", payload)
        self.assertIn("trusted", payload)

    def test_jira_file_is_accepted(self) -> None:
        fixture = ROOT / ".ai-harness" / "evals" / "_test_jira.txt"
        fixture.write_text("PROJ-1: Add tenant filtering\nAcceptance: preserve existing behavior", encoding="utf-8")
        try:
            result = self.run_cli("run", "--jira-file", str(fixture), "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            fixture.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
