"""GitHub Actions evidence recorder binds outcomes to the exact workflow head."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.record_ci_evidence import main


class RecordCiEvidenceTests(unittest.TestCase):
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
