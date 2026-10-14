from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "validate_auren_identity.py"


class IdentityAuditReportTests(unittest.TestCase):
    def test_report_inventories_approved_legacy_matches_without_weakening_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "identity-audit.json"
            result = subprocess.run(
                [sys.executable, str(AUDIT), "--report-json", str(report_path)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema_version"], "auren-identity-audit-report-v1")
            self.assertTrue(report["audit_passed"])
            self.assertGreater(report["tracked_file_count"], 0)
            self.assertGreater(report["allowlisted_match_count"], 0)
            self.assertEqual(report["unapproved_match_count"], 0)
            self.assertTrue(any(
                item["path"] == "portable/identity_compat.py"
                and item["disposition"] == "file_allowlisted"
                for item in report["match_inventory"]
            ))
            self.assertTrue(any(
                item["disposition"] == "line_allowlisted"
                for item in report["match_inventory"]
            ))

    def test_report_refuses_to_write_inside_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(AUDIT), "--report-json", str(ROOT / "identity-audit.json")],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("outside the repository", result.stdout)
            self.assertFalse((ROOT / "identity-audit.json").exists())


if __name__ == "__main__":
    unittest.main()
