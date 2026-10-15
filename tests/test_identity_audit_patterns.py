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
    def test_python_symbol_scan_ignores_strings_and_flags_legacy_identifiers(self):
        source = "def legacy_aer_home():\\n    auren_state = \"legacy_aer_home\"\\n    return auren_state\\n"
        self.assertEqual(VALIDATOR.find_legacy_python_symbols(source), [(1, "legacy_aer_home")])
        self.assertEqual(VALIDATOR.find_legacy_python_symbols("class ArenaError:\\n    pass\\n"), [])

    def test_path_token_scan_flags_legacy_file_and_folder_names(self):
        self.assertIsNotNone(VALIDATOR.LEGACY_PATH_TOKEN.search("src/aer_runtime/main.py"))
        self.assertIsNotNone(VALIDATOR.LEGACY_PATH_TOKEN.search("src/.aer/config.json"))
        self.assertIsNone(VALIDATOR.LEGACY_PATH_TOKEN.search("portable/independent_arena.py"))
        self.assertIsNone(VALIDATOR.LEGACY_PATH_TOKEN.search("tests/test_aer_runtime.py"))

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
