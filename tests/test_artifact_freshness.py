import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

from artifact_freshness import build_artifact_manifest, validate_artifact_freshness


class ArtifactFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, timeout=5)
        self.source = self.root / "source.py"
        self.source.write_text("VALUE = 1\n", encoding="utf-8")
        self.artifact = self.root / "graph.json"
        self.artifact.write_text('{"nodes": []}\n', encoding="utf-8")
        self.manifest = self.artifact.with_name(self.artifact.name + ".auren-manifest.json")

    def write_manifest(self):
        self.manifest.write_text(
            json.dumps(build_artifact_manifest(self.artifact, self.root)),
            encoding="utf-8",
        )

    def test_manifest_proves_exact_artifact_and_repository_snapshot(self):
        self.write_manifest()
        result = validate_artifact_freshness(self.artifact, self.root)
        self.assertTrue(result["fresh"])
        self.assertEqual("fresh", result["status"])
        self.assertEqual(64, len(result["artifact_sha256"]))
        self.assertEqual(64, len(result["repository_snapshot_sha256"]))

    def test_source_edit_invalidates_repository_snapshot(self):
        self.write_manifest()
        self.source.write_text("VALUE = 2\n", encoding="utf-8")
        result = validate_artifact_freshness(self.artifact, self.root)
        self.assertFalse(result["fresh"])
        self.assertEqual("stale", result["status"])
        self.assertEqual("repository_snapshot_digest_mismatch", result["reason"])

    def test_artifact_edit_invalidates_artifact_digest(self):
        self.write_manifest()
        self.artifact.write_text('{"nodes": ["changed"]}\n', encoding="utf-8")
        result = validate_artifact_freshness(self.artifact, self.root)
        self.assertFalse(result["fresh"])
        self.assertEqual("artifact_digest_mismatch", result["reason"])

    def test_missing_manifest_is_unverified_not_fresh(self):
        result = validate_artifact_freshness(self.artifact, self.root)
        self.assertFalse(result["fresh"])
        self.assertEqual("unverified", result["status"])
        self.assertEqual("freshness_manifest_missing", result["reason"])

    def test_artifacts_outside_repository_are_rejected(self):
        outside = self.root.parent / f"{self.root.name}-outside-graph.json"
        outside.write_text("{}", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with self.assertRaisesRegex(ValueError, "inside the repository"):
            build_artifact_manifest(outside, self.root)
        result = validate_artifact_freshness(outside, self.root)
        self.assertFalse(result["fresh"])
        self.assertEqual("invalid", result["status"])


if __name__ == "__main__":
    unittest.main()
