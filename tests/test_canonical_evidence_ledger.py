from contextlib import closing
import multiprocessing
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.canonical_evidence_ledger import CanonicalEvidenceLedger, EvidenceConflict


HEAD = "b" * 40
OTHER_HEAD = "c" * 40
PAYLOAD_DIGEST = "a" * 64


def _record_evidence_then_exit(database: str) -> None:
    ledger = CanonicalEvidenceLedger(database)
    ledger.record(
        evidence_id="ci:crash-after-commit",
        repository="sourabhJainR/AUREN",
        head_sha=HEAD,
        evidence_type="ci_check",
        status="passed",
        source_uri="https://github.com/sourabhJainR/AUREN/actions/runs/crash-fixture",
        payload_digest=PAYLOAD_DIGEST,
        signer_id="operator-ci",
    )
    # The durable row is committed, but the parent never receives the return
    # value. This models a caller crash at the commit/acknowledgement boundary.
    os._exit(86)


class CanonicalEvidenceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "evidence.sqlite3"
        self.ledger = CanonicalEvidenceLedger(self.database)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def record(self, **overrides):
        values = {
            "evidence_id": "ci:architecture:run-1",
            "repository": "sourabhJainR/AUREN",
            "head_sha": HEAD,
            "evidence_type": "ci_check",
            "status": "passed",
            "source_uri": "https://github.com/sourabhJainR/AUREN/actions/runs/1",
            "payload_digest": PAYLOAD_DIGEST,
            "signer_id": "operator-ci",
        }
        values.update(overrides)
        return self.ledger.record(**values)

    def test_exact_head_evidence_is_resolvable_after_reopen(self):
        created = self.record()
        reopened = CanonicalEvidenceLedger(self.database)
        self.assertEqual(reopened.known_evidence_ids(repository="sourabhJainR/AUREN", head_sha=HEAD), (created["evidence_id"],))
        resolved = reopened.resolve(created["evidence_id"], repository="sourabhJainR/AUREN", head_sha=HEAD)
        self.assertEqual(resolved["record_digest"], created["record_digest"])
        self.assertIsNone(reopened.resolve(created["evidence_id"], repository="sourabhJainR/AUREN", head_sha=OTHER_HEAD))


    def test_process_death_after_evidence_commit_allows_idempotent_retry(self):
        process = multiprocessing.get_context("spawn").Process(
            target=_record_evidence_then_exit,
            args=(str(self.database),),
        )
        process.start()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(3)
            self.fail("evidence commit crash fixture exceeded its time budget")
        self.assertEqual(86, process.exitcode)

        reopened = CanonicalEvidenceLedger(self.database)
        recovered = reopened.record(
            evidence_id="ci:crash-after-commit",
            repository="sourabhJainR/AUREN",
            head_sha=HEAD,
            evidence_type="ci_check",
            status="passed",
            source_uri="https://github.com/sourabhJainR/AUREN/actions/runs/crash-fixture",
            payload_digest=PAYLOAD_DIGEST,
            signer_id="operator-ci",
        )
        self.assertEqual("ci:crash-after-commit", recovered["evidence_id"])
        self.assertEqual(
            ("ci:crash-after-commit",),
            reopened.known_evidence_ids(repository="sourabhJainR/AUREN", head_sha=HEAD),
        )
        with closing(sqlite3.connect(self.database)) as db, db:
            count = db.execute(
                "SELECT COUNT(*) FROM canonical_evidence WHERE evidence_id=?",
                ("ci:crash-after-commit",),
            ).fetchone()[0]
        self.assertEqual(1, count)
        self.assertEqual(
            recovered["record_digest"],
            reopened.resolve("ci:crash-after-commit", repository="sourabhJainR/AUREN", head_sha=HEAD)["record_digest"],
        )


    def test_resolve_rejects_mutated_persisted_record(self):
        created = self.record()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "UPDATE canonical_evidence SET status='failed' WHERE evidence_id=?",
                (created["evidence_id"],),
            )
        reopened = CanonicalEvidenceLedger(self.database)
        with self.assertRaisesRegex(ValueError, "record digest is corrupt"):
            reopened.resolve(created["evidence_id"], repository="sourabhJainR/AUREN", head_sha=HEAD)
        with self.assertRaisesRegex(ValueError, "record digest is corrupt"):
            reopened.record(
                evidence_id=created["evidence_id"],
                repository="sourabhJainR/AUREN",
                head_sha=HEAD,
                evidence_type="ci_check",
                status="passed",
                source_uri="https://github.com/sourabhJainR/AUREN/actions/runs/1",
                payload_digest=PAYLOAD_DIGEST,
                signer_id="operator-ci",
            )

    def test_promotion_lookup_rejects_tampered_status(self):
        created = self.record(evidence_id="tampered-status", status="failed")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "UPDATE canonical_evidence SET status='passed' WHERE evidence_id=?",
                (created["evidence_id"],),
            )
        with self.assertRaisesRegex(ValueError, "record digest is corrupt"):
            self.ledger.known_evidence_ids(repository="sourabhJainR/AUREN", head_sha=HEAD)


    def test_promotion_lookup_rejects_rebound_head(self):
        created = self.record(evidence_id="tampered-head", head_sha=OTHER_HEAD)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "UPDATE canonical_evidence SET head_sha=? WHERE evidence_id=?",
                (HEAD, created["evidence_id"]),
            )
        with self.assertRaisesRegex(ValueError, "record digest is corrupt"):
            self.ledger.known_evidence_ids(repository="sourabhJainR/AUREN", head_sha=HEAD)

    def test_same_evidence_id_cannot_be_rebound_to_different_content(self):
        first = self.record()
        repeated = self.record()
        self.assertEqual(first["record_digest"], repeated["record_digest"])
        with self.assertRaises(EvidenceConflict):
            self.record(payload_digest="d" * 64)

    def test_other_repository_and_stale_head_are_not_known(self):
        self.record()
        self.record(evidence_id="ci:old-head", head_sha=OTHER_HEAD)
        self.assertEqual(self.ledger.known_evidence_ids(repository="another/repo", head_sha=HEAD), ())
        self.assertEqual(self.ledger.known_evidence_ids(repository="sourabhJainR/AUREN", head_sha=OTHER_HEAD), ("ci:old-head",))

    def test_invalid_head_status_digest_and_source_fail_closed(self):
        for overrides in (
            {"head_sha": "short"},
            {"status": "success"},
            {"payload_digest": "not-a-digest"},
            {"source_uri": "javascript:alert(1)"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.record(evidence_id="invalid-" + str(len(overrides)) + str(overrides), **overrides)

    def test_failed_skipped_and_unknown_evidence_never_enters_promotion_context(self):
        self.record(evidence_id="passed")
        self.record(evidence_id="failed", status="failed")
        self.record(evidence_id="skipped", status="skipped")
        self.record(evidence_id="unknown", status="unknown")
        self.record(evidence_id="verified", status="verified")
        self.assertEqual(
            self.ledger.known_evidence_ids(repository="sourabhJainR/AUREN", head_sha=HEAD),
            ("passed", "verified"),
        )

    def test_in_memory_database_is_rejected(self):
        with self.assertRaises(ValueError):
            CanonicalEvidenceLedger(":memory:")


if __name__ == "__main__":
    unittest.main()
