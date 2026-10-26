from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.state_migration_rehearsal import capture_migration_rehearsal


REV_A = "a" * 40
REV_B = "b" * 40


class StateMigrationRehearsalEvidenceTests(unittest.TestCase):
    def test_dry_run_is_non_mutating_and_never_claims_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "legacy"
            destination = root / "candidate"
            source.mkdir()
            (source / "session.txt").write_text("retained state", encoding="utf-8")
            report = capture_migration_rehearsal(
                source=source, destination=destination, operator_id="staging-operator",
                candidate_revision=REV_A, last_known_good_revision=REV_B, mode="dry-run",
            )
            self.assertEqual("dry_run_only", report["status"])
            self.assertFalse(destination.exists())
            self.assertTrue(report["source_unchanged"])
            self.assertFalse(report["completion_claim_permitted"])
            self.assertFalse(report["runtime_resume_verified"])
            self.assertFalse(report["rollback_rehearsal_verified"])

    def test_apply_verifies_copy_sqlite_and_source_retention_but_not_runtime_cutover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "legacy"
            destination = root / "candidate"
            source.mkdir()
            (source / "session.txt").write_text("session data", encoding="utf-8")
            with sqlite3.connect(source / "evidence.sqlite3") as db:
                db.execute("CREATE TABLE evidence (id TEXT PRIMARY KEY)")
                db.execute("INSERT INTO evidence VALUES ('receipt-1')")
            report = capture_migration_rehearsal(
                source=source, destination=destination, operator_id="staging-operator",
                candidate_revision=REV_A, last_known_good_revision=REV_B, mode="apply",
            )
            self.assertEqual("file_copy_verified_operator_runtime_checks_required", report["status"])
            self.assertTrue(report["file_copy_verified"])
            self.assertTrue(report["destination_manifest_verified"])
            self.assertTrue(report["source_unchanged"])
            self.assertEqual("passed", report["sqlite_integrity"][0]["status"])
            self.assertTrue((source / "session.txt").exists())
            self.assertTrue((destination / "session.txt").exists())
            self.assertFalse(report["runtime_resume_verified"])
            self.assertFalse(report["rollback_rehearsal_verified"])
            self.assertFalse(report["post_cutover_write_reconciliation_verified"])
            self.assertFalse(report["completion_claim_permitted"])

    def test_invalid_operator_or_revision_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "legacy"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "operator_id"):
                capture_migration_rehearsal(
                    source=source, destination=Path(tmp) / "candidate", operator_id="",
                    candidate_revision=REV_A, last_known_good_revision=REV_B,
                )
            with self.assertRaisesRegex(ValueError, "full lowercase Git revision"):
                capture_migration_rehearsal(
                    source=source, destination=Path(tmp) / "candidate", operator_id="operator",
                    candidate_revision="bad", last_known_good_revision=REV_B,
                )


if __name__ == "__main__":
    unittest.main()
