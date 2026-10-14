from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "auren_identity_validator", ROOT / "scripts" / "validate_auren_identity.py"
)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class CaseInsensitiveIdentityAuditTests(unittest.TestCase):
    def test_environment_and_internal_identifiers_are_case_insensitive(self):
        samples = (
            "AER_NETWORK_ALLOWED",
            "Aer_Network_Allowed",
            "aer_network_allowed",
            "AER_execution_strategy",
            "Aer_Cli.py",
            "AER-PORTABLE",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(
                    any(pattern.search(sample) for pattern in VALIDATOR.PATTERNS[1:]),
                    f"legacy identifier escaped the audit: {sample}",
                )


if __name__ == "__main__":
    unittest.main()
