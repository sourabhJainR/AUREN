"""Live Git context provider must ignore caller-supplied repository/head claims."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from portable.canonical_evidence_ledger import CanonicalEvidenceLedger
from portable.trusted_repository_context import TrustedRepositoryContextProvider


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()


class TrustedRepositoryContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(self.repo)], check=True, capture_output=True)
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "user.name", "AUREN tests")
        (self.repo / "tracked.txt").write_text("committed\n", encoding="utf-8")
        git(self.repo, "add", "tracked.txt")
        git(self.repo, "commit", "-m", "initial")
        git(self.repo, "remote", "add", "origin", "https://github.com/owner/project.git")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.records = self.base / "operator-records"
        self.records.mkdir()
        self.approvers = self.base / "approvers.json"
        self.approvers.write_text(json.dumps(["release-manager"]), encoding="utf-8")
        self.ledger = CanonicalEvidenceLedger(self.base / "evidence.sqlite3")
        for evidence_id, evidence_type in (("ci-run-1", "ci_check"), ("approval-1", "approval"), ("rollback-1", "rollback_rehearsal")):
            self.ledger.record(
                evidence_id=evidence_id,
                repository="owner/project",
                head_sha=self.head,
                evidence_type=evidence_type,
                status="verified",
                source_uri="urn:auren:test:" + evidence_id,
                payload_digest="a" * 64,
                signer_id="test-signer",
            )
        self.record = {
            "schema_version": "1.0",
            "task_id": "task-1",
            "repository": "owner/project",
            "base_commit": "0" * 40,
            "head_commit": self.head,
            "risk_level": "high",
            "acceptance_criteria": ["exact-head validation"],
            "selected_option": "guarded promotion",
            "selection_rationale": "trusted live context",
            "alternatives": [{"option": "skip gate", "reason_rejected": "unsafe"}],
            "dependencies": [],
            "checks": [{
                "name": "required-ci", "command": "CI", "status": "passed",
                "commit_sha": self.head, "evidence_id": "ci-run-1",
                "evaluator_type": "external",
            }],
            "limitations": ["external operator still owns the evidence system"],
            "rollback_trigger": "any required check fails",
            "rollback_action": "restore last known-good revision",
            "rollback_procedure_ref": "runbook://rollback",
            "rollback_test_evidence_id": "rollback-1",
            "last_known_good_commit": "0" * 40,
            "retry_history": [],
            "promotion": {
                "level": "verified_for_scope", "approved_by": "release-manager",
                "approval_evidence_id": "approval-1",
            },
        }
        (self.records / "task-1.json").write_text(json.dumps(self.record), encoding="utf-8")
        self.provider = TrustedRepositoryContextProvider(
            self.repo, expected_repository="owner/project",
            decision_record_dir=self.records, authorized_approvers_file=self.approvers,
            evidence_ledger=self.ledger,
        )

    def test_live_repository_and_head_override_spoofed_request_fields(self):
        context = self.provider({
            "task_id": "task-1",
            "expected_repository": "attacker/other",
            "expected_head": "f" * 40,
            "payload": {"decision_record": {"approved": True}},
        })
        self.assertEqual(context["expected_repository"], "owner/project")
        self.assertEqual(context["expected_head"], self.head)
        self.assertEqual(set(context["known_evidence_ids"]), {"ci-run-1", "approval-1", "rollback-1"})
        self.assertEqual(context["authorized_approvers"], ("release-manager",))

    def test_dirty_worktree_fails_closed(self):
        (self.repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.provider({"task_id": "task-1"})

    def test_wrong_origin_fails_closed(self):
        git(self.repo, "remote", "set-url", "origin", "https://github.com/attacker/other.git")
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.provider({"task_id": "task-1"})

    def test_unsafe_task_identifier_is_rejected_before_path_resolution(self):
        with self.assertRaisesRegex(ValueError, "safe task identifier"):
            self.provider({"task_id": "../task-1"})

    def test_evidence_from_another_head_is_not_resolved(self):
        self.ledger.record(
            evidence_id="stale-evidence", repository="owner/project",
            head_sha="b" * 40, evidence_type="test", status="verified",
            source_uri="urn:auren:stale", payload_digest="c" * 64, signer_id="test-signer",
        )
        context = self.provider({"task_id": "task-1"})
        self.assertNotIn("stale-evidence", context["known_evidence_ids"])


if __name__ == "__main__":
    unittest.main()
