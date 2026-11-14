from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from portable.integration_acceptance import (
    IntegrationAcceptanceError,
    integration_acceptance_report,
    record_integration_acceptance,
)


NOW = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)


class IntegrationAcceptanceReceiptTests(unittest.TestCase):
    def record(self, root: str, **overrides):
        values = {
            "integration_id": "codex-cli",
            "integration_kind": "mcp_host",
            "status": "pass",
            "product_version": "1.2.3",
            "protocol_version": "2025-11-25",
            "test_scope": ["initialize", "tools_list", "oauth_refresh"],
            "evidence_ref": "artifacts/codex-acceptance.txt",
            "evidence_sha256": "a" * 64,
            "source": "operator_attested",
            "confirm": True,
            "apply": True,
            "now": NOW,
        }
        values.update(overrides)
        return record_integration_acceptance(root, **values)

    def test_preview_does_not_write_and_apply_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            preview = self.record(directory, apply=False, confirm=False)
            path = Path(preview["path"])
            self.assertEqual("preview", preview["status"])
            self.assertFalse(path.exists())
            with self.assertRaisesRegex(IntegrationAcceptanceError, "explicit confirmation"):
                self.record(directory, confirm=False)

    def test_recorded_receipt_is_digest_bound_and_operator_attested(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.record(directory)
            report = integration_acceptance_report(directory, now=NOW)
        self.assertEqual("recorded", result["status"])
        self.assertEqual(1, result["revision"])
        self.assertEqual("fresh_pass", report["summary"]["fresh_pass"] and "fresh_pass")
        receipt = report["receipts"][0]
        self.assertEqual("fresh", receipt["freshness"])
        self.assertEqual("operator_attested_not_independently_verified", receipt["trust_level"])
        self.assertEqual("codex-cli", receipt["integration_id"])

    def test_stale_and_future_receipts_are_not_reported_as_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            self.record(directory, now=NOW - timedelta(days=31))
            stale = integration_acceptance_report(directory, now=NOW)
        self.assertEqual("stale", stale["receipts"][0]["freshness"])
        self.assertEqual(1, stale["summary"]["stale"])

        with tempfile.TemporaryDirectory() as directory:
            self.record(directory, now=NOW + timedelta(days=1))
            future = integration_acceptance_report(directory, now=NOW)
        self.assertEqual("invalid", future["receipts"][0]["freshness"])
        self.assertEqual(1, future["summary"]["invalid"])

    def test_receipt_digest_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            self.record(directory)
            path = Path(directory) / ".auren" / "integration-acceptance.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            state["receipts"][0]["product_version"] = "9.9.9"
            path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(IntegrationAcceptanceError, "digest mismatch"):
                integration_acceptance_report(directory, now=NOW)

    def test_same_integration_record_replaces_prior_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            self.record(directory)
            result = self.record(directory, product_version="1.2.4", evidence_sha256="b" * 64)
            report = integration_acceptance_report(directory, now=NOW)
        self.assertEqual(2, result["revision"])
        self.assertEqual(1, len(report["receipts"]))
        self.assertEqual("1.2.4", report["receipts"][0]["product_version"])

    def test_builtin_probe_receipt_is_reserved_for_local_auren_mcp(self):
        with tempfile.TemporaryDirectory() as directory:
            result = record_integration_acceptance(
                directory,
                integration_id="auren-local-mcp",
                integration_kind="local_mcp_service",
                status="pass",
                product_version="1.0.0",
                protocol_version="2025-11-25",
                test_scope=["initialize", "tools_list"],
                evidence_ref="builtin:probe-local-mcp",
                evidence_sha256="c" * 64,
                source="builtin_probe",
                apply=True,
                now=NOW,
            )
            report = integration_acceptance_report(directory, now=NOW)
        self.assertEqual("recorded", result["status"])
        self.assertEqual(
            "builtin_probe_not_external_host_acceptance",
            report["receipts"][0]["trust_level"],
        )

    def test_builtin_probe_cannot_claim_external_host_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(IntegrationAcceptanceError, "reserved"):
                record_integration_acceptance(
                    directory,
                    integration_id="codex-cli",
                    integration_kind="mcp_host",
                    status="pass",
                    product_version="1.2.3",
                    protocol_version=None,
                    test_scope=["tools_list"],
                    evidence_ref="builtin:probe-local-mcp",
                    evidence_sha256="c" * 64,
                    source="builtin_probe",
                    apply=True,
                    now=NOW,
                )

    def test_invalid_ids_scopes_and_evidence_paths_are_rejected(self):
        cases = [
            {"integration_id": "../codex"},
            {"test_scope": ["tools_list", "tools_list"]},
            {"test_scope": ["../tools"]},
            {"evidence_ref": "../outside.log"},
            {"evidence_ref": "https://example.com/report?token=secret"},
            {"evidence_sha256": "not-a-digest"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            for case in cases:
                with self.subTest(case=case):
                    with self.assertRaises(IntegrationAcceptanceError):
                        self.record(directory, apply=False, **case)

    def test_duplicate_json_keys_and_symlinked_receipt_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".auren" / "integration-acceptance.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"schema_version":1,"schema_version":1,"revision":0,"receipts":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(IntegrationAcceptanceError, "duplicate JSON keys"):
                integration_acceptance_report(directory, now=NOW)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".auren" / "integration-acceptance.json"
            path.parent.mkdir(parents=True)
            target = Path(directory) / "target.json"
            target.write_text('{"schema_version":1,"revision":0,"receipts":[]}', encoding="utf-8")
            try:
                path.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("file symlinks are unavailable on this platform")
            with self.assertRaisesRegex(IntegrationAcceptanceError, "symlinked"):
                integration_acceptance_report(directory, now=NOW)

    def test_stale_writer_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".auren" / "integration-acceptance.json"
            path.parent.mkdir(parents=True)
            path.with_name(path.name + ".lock").write_text("1234", encoding="ascii")
            with self.assertRaisesRegex(IntegrationAcceptanceError, "stale lock"):
                self.record(directory)


if __name__ == "__main__":
    unittest.main()
