from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from portable.identity_compat import auren_home, canonical_auren_home, canonical_runtime_home, canonicalize_auren_checkpoint_state, get_auren_env, legacy_state_home, project_state_dir
from portable.local_llm import LocalLLMConfig, enabled as local_llm_enabled
from portable.chat_capability import _default_runtime
from portable.orchestration import Orchestrator


class IdentityCompatibilityTests(unittest.TestCase):
    def test_orchestration_reads_legacy_adaptive_policy_only_via_compatibility_adapter(self) -> None:
        legacy_policy = {
            "version": "v1",
            "strategy": "balanced",
            "iteration_target": 3.0,
        }
        self.assertEqual(
            Orchestrator._read_adaptive_policy({"aer_adaptive_policy": legacy_policy}),
            legacy_policy,
        )
        with self.assertRaisesRegex(ValueError, "conflicting legacy and canonical"):
            Orchestrator._read_adaptive_policy({
                "aer_adaptive_policy": legacy_policy,
                "auren_adaptive_policy": {
                    "version": "v2",
                    "strategy": "aggressive",
                    "iteration_target": 4.0,
                },
            })

    def test_checkpoint_key_migration_is_versioned_idempotent_and_conflict_safe(self) -> None:
        legacy = {
            "aer_execution_strategy": {"name": "default"},
            "aer_execution_mode": {"name": "balanced"},
            "aer_context_pack": {"budget": 1200},
        }
        migrated = canonicalize_auren_checkpoint_state(legacy)
        self.assertEqual(migrated["auren_execution_strategy"], {"name": "default"})
        self.assertEqual(migrated["auren_execution_mode"], {"name": "balanced"})
        self.assertEqual(migrated["auren_context_pack"], {"budget": 1200})
        self.assertNotIn("aer_execution_strategy", migrated)
        self.assertEqual(migrated["auren_state_schema_version"], 2)
        self.assertEqual(canonicalize_auren_checkpoint_state(migrated), migrated)
        with self.assertRaisesRegex(ValueError, "conflicting legacy and canonical"):
            canonicalize_auren_checkpoint_state({
                "aer_execution_mode": {"name": "legacy"},
                "auren_execution_mode": {"name": "canonical"},
            })
        with self.assertRaisesRegex(ValueError, "unsupported AUREN checkpoint state schema"):
            canonicalize_auren_checkpoint_state({"auren_state_schema_version": 99})

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

    def test_local_llm_config_uses_canonical_setting_before_legacy(self) -> None:
        with patch.dict(os.environ, {
            "AUREN_LOCAL_LLM_MODEL": "canonical-model",
            "AER_LOCAL_LLM_MODEL": "legacy-model",
            "AER_LOCAL_LLM_ENABLED": "1",
        }, clear=True):
            self.assertEqual(LocalLLMConfig.from_env().model, "canonical-model")
            self.assertTrue(local_llm_enabled())
        with patch.dict(os.environ, {"AER_LOCAL_LLM_MODEL": "legacy-model"}, clear=True):
            self.assertEqual(LocalLLMConfig.from_env().model, "legacy-model")

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
                Path.home() / "chosen",
            )

    def test_canonical_state_root_ignores_legacy_home_and_rejects_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                canonical_auren_home(home=root, environ={"AER_HOME": str(root / "legacy")}),
                root / ".auren",
            )
            self.assertEqual(
                canonical_auren_home(home=root, environ={"AUREN_HOME": str(root / "chosen")}),
                root / "chosen",
            )
        with self.assertRaisesRegex(ValueError, "AUREN_HOME must not be empty"):
            canonical_auren_home(environ={"AUREN_HOME": "", "AER_HOME": "/tmp/legacy"})

    def test_legacy_state_root_rejects_empty(self) -> None:
        with self.assertRaisesRegex(ValueError, "legacy AER_HOME must not be empty"):
            legacy_state_home(environ={"AER_HOME": ""})

    def test_empty_configured_home_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "AUREN_HOME must not be empty"):
            auren_home(environ={"AUREN_HOME": "", "AER_HOME": "/tmp/legacy"})
        with self.assertRaisesRegex(ValueError, "legacy AER_HOME must not be empty"):
            auren_home(environ={"AER_HOME": ""})

    def test_chat_runtime_honors_canonical_home_over_legacy_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical"
            legacy = root / "legacy"
            with patch.dict(os.environ, {
                "AUREN_HOME": str(canonical),
                "AER_HOME": str(legacy),
            }, clear=True):
                with patch("portable.chat_capability.AutomationScheduler") as scheduler, patch(
                    "portable.chat_capability.AdaptiveRuntime"
                ):
                    _default_runtime()
            scheduler.assert_called_once_with(canonical / "automation" / "automation.db")

    def test_canonical_runtime_home_migrates_legacy_orchestration_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / ".aer"
            source = legacy / "orchestration" / "active.py"
            source.parent.mkdir(parents=True)
            source.write_text("PLAN_VERSION = 7\n", encoding="utf-8")

            canonical = canonical_runtime_home(home=root, environ={})

            self.assertEqual(canonical, root / ".auren")
            self.assertEqual((canonical / "orchestration" / "active.py").read_text(encoding="utf-8"), "PLAN_VERSION = 7\n")
            self.assertTrue(source.is_file(), "legacy state must remain for rollback")

    def test_canonical_runtime_home_migrates_explicit_legacy_home_to_explicit_canonical_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "legacy-aer"
            source = legacy / "orchestration" / "active.py"
            source.parent.mkdir(parents=True)
            source.write_text("PLAN_VERSION = 11\\n", encoding="utf-8")
            canonical = root / "canonical-auren"

            result = canonical_runtime_home(
                home=root,
                environ={"AUREN_HOME": str(canonical), "AER_HOME": str(legacy)},
            )

            self.assertEqual(result, canonical)
            self.assertEqual(
                (canonical / "orchestration" / "active.py").read_text(encoding="utf-8"),
                "PLAN_VERSION = 11\\n",
            )
            self.assertTrue(source.is_file(), "legacy state must remain available for rollback")

    def test_canonical_runtime_home_rejects_overlapping_explicit_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp) / "same-root"
            with self.assertRaisesRegex(ValueError, "must be distinct, non-overlapping"):
                canonical_runtime_home(
                    environ={"AUREN_HOME": str(shared), "AER_HOME": str(shared)},
                )
            self.assertFalse(shared.exists(), "invalid root configuration must fail before creating state")

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
