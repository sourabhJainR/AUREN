from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.guarded_filesystem import write_workspace_text
from portable.vibe_coding_host import LocalAccessibilityTool


class LargeContextIntegrationTests(unittest.TestCase):
    def test_accessibility_scan_streams_large_artifact_to_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            # Keep the required terms far apart so a first/last preview is not enough.
            source = "keyboard\n" + ("unrelated content without requirements\n" * 2500)
            source += "focus\n" + ("more unrelated material\n" * 2500)
            source += "labels\n" + ("still unrelated\n" * 2500)
            source += "contrast\n" + ("tail material\n" * 2500) + "error\n"
            write_workspace_text(root, "docs/design.md", source)
            result = LocalAccessibilityTool(root)({
                "prior_stage_outputs": {"prd": {"design_artifacts": ["docs/design.md"]}}
            })
            self.assertEqual("passed", result["accessibility_status"])
            self.assertEqual([], result["accessibility_findings"])

    def test_accessibility_scan_keeps_guarded_path_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            outside = Path(directory) / "secret.md"
            outside.write_text("keyboard focus labels contrast error", encoding="utf-8")
            result = LocalAccessibilityTool(root)({
                "prior_stage_outputs": {"prd": {"design_artifacts": ["../secret.md"]}}
            })
            self.assertEqual("failed", result["accessibility_status"])
            self.assertTrue(any(item["severity"] == "high" for item in result["accessibility_findings"]))


if __name__ == "__main__":
    unittest.main()
