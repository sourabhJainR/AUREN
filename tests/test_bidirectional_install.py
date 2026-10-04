from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portable.auren_runtime import build, install


class BidirectionalInstallTests(unittest.TestCase):
    def _source(self, root: Path, version: str, marker: str) -> None:
        (root / ".ai-harness").mkdir(parents=True, exist_ok=True)
        (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (root / "skills" / "ai-coding-orchestrator").mkdir(parents=True, exist_ok=True)
        (root / "portable").mkdir(parents=True, exist_ok=True)
        (root / "dashboard").mkdir(parents=True, exist_ok=True)
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}), encoding="utf-8")
        (root / ".ai-harness" / "config.toml").write_text("version = 20\n", encoding="utf-8")
        (root / ".ai-harness" / "marker.txt").write_text(marker, encoding="utf-8")
        (root / "skills" / "ai-coding-orchestrator" / "SKILL.md").write_text("skill\n", encoding="utf-8")
        (root / "portable" / "auren_runtime.py").write_text("print('runtime')\n", encoding="utf-8")
        (root / "dashboard" / "index.html").write_text("<html>AUREN Engineering Console</html>\n", encoding="utf-8")
        (root / "auren_cli.py").write_text("print('launcher')\n", encoding="utf-8")

    def test_explicit_old_artifact_can_replace_new_active_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            home = Path(tmp) / "auren-home"
            self._source(root, "20.2.0", "new")
            new_bundle = Path(tmp) / "new.zip"
            build(root, new_bundle, source_commit="new-commit")
            install(new_bundle, "none", home)

            self._source(root, "20.1.1", "old")
            old_bundle = Path(tmp) / "old.zip"
            build(root, old_bundle, source_commit="old-commit")
            install(old_bundle, "none", home)

            active = json.loads((home / "active.json").read_text(encoding="utf-8"))
            self.assertEqual(active["version"], "20.1.1")
            self.assertEqual(active["source_commit"], "old-commit")
            versions = list((home / "versions").iterdir())
            self.assertEqual(len(versions), 2)


if __name__ == "__main__":
    unittest.main()
