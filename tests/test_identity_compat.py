from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.identity_compat import auren_home, get_auren_env, project_state_dir


class IdentityCompatibilityTests(unittest.TestCase):
    def test_canonical_environment_precedes_legacy_alias(self) -> None:
        env = {"AUREN_NETWORK_ALLOWED": "0", "AER_NETWORK_ALLOWED": "1"}
        self.assertEqual(get_auren_env("NETWORK_ALLOWED", "1", environ=env), "0")

    def test_legacy_environment_is_fallback_only(self) -> None:
        self.assertEqual(
            get_auren_env("LOCAL_LLM_ENABLED", "0", environ={"AER_LOCAL_LLM_ENABLED": "1"}),
            "1",
        )
        self.assertEqual(
            get_auren_env("LOCAL_LLM_ENABLED", "0", environ={}),
            "0",
        )

    def test_home_selection_preserves_existing_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(auren_home(home=root, environ={}), root / ".auren")
            (root / ".aer").mkdir()
            self.assertEqual(auren_home(home=root, environ={}), root / ".aer")
            (root / ".auren").mkdir()
            self.assertEqual(auren_home(home=root, environ={}), root / ".auren")
            self.assertEqual(
                auren_home(home=root, environ={"AUREN_HOME": "~/chosen", "AER_HOME": "~/legacy"}),
                (Path.home() / "chosen").resolve(),
            )

    def test_project_state_prefers_canonical_then_legacy_without_moving_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / ".aer"
            legacy.mkdir()
            self.assertEqual(project_state_dir(root), legacy)
            canonical = root / ".auren"
            canonical.mkdir()
            self.assertEqual(project_state_dir(root), canonical)
            self.assertTrue(legacy.is_dir())



if __name__ == "__main__":
    unittest.main()
