"""GitHub Actions evidence recorder binds outcomes to the exact workflow head."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.record_ci_evidence import _external_sink_policy, main


class RecordCiEvidenceTests(unittest.TestCase):
    def test_external_sink_policy_warns_by_default_and_can_be_required(self):
        self.assertEqual(_external_sink_policy({}), (False, False))
        self.assertEqual(
            _external_sink_policy({"AUREN_REQUIRE_EXTERNAL_EVIDENCE_SINK": "true"}),
            (False, True),
        )
        self.assertEqual(
            _external_sink_policy({
                "AUREN_EVIDENCE_SINK_URL": "https://archive.example/v1/auren/events",
                "AUREN_EVIDENCE_SINK_TOKEN": "token",
            }),
            (True, False),
        )

    def test_partial_external_sink_configuration_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "must be configured together"):
            _external_sink_policy({"AUREN_EVIDENCE_SINK_URL": "https://archive.example/v1/auren/events"})

    def test_successful_ci_writes_safe_exact_head_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "AUREN_CI_REPOSITORY": "owner/project",
                "AUREN_CI_HEAD_SHA": "b" * 40,
                "AUREN_CI_RUN_ID": "1234",
                "AUREN_CI_ATTEMPT": "2",
                "AUREN_CI_JOB": "architecture-integrity",
                "AUREN_CI_WORKFLOW": "Architecture Integrity",
                "AUREN_CI_STATUS": "success",
            }
            with patch.dict(os.environ, env, clear=True):
                old_cwd = os.getcwd()
                try:
                    os.chdir(temp)
                    self.assertEqual(main(), 0)
                    output = json.loads(Path(".auren/ci-engineering-evidence.json").read_text(encoding="utf-8"))
                finally:
                    os.chdir(old_cwd)
            self.assertEqual(output["event_type"], "regression_passed")
            self.assertEqual(output["head_sha"], "b" * 40)
            self.assertEqual(output["repository"], "owner/project")
            self.assertEqual(output["metadata"]["run_id"], "1234:2:architecture-integrity")
            self.assertNotIn("raw_output", output)


if __name__ == "__main__":
    unittest.main()
