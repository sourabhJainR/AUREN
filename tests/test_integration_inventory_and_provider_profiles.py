from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".ai-harness"))

from portable.crew_adapters import ApprovedToolRegistry
from portable.progress_journal import ProgressJournal
import runtime
from portable.integration_inventory import (
    _client_configuration,
    _remote_provider_inventory,
    _strip_jsonc,
    collect_integration_inventory,
)
from portable.provider_role_profiles import (
    PROFILE_ROLES,
    ProviderRoleProfileError,
    _parse_profile,
    _prompt_privacy,
    _role_invoker,
    build_profiled_crew_adapter_inputs,
    create_provider_role_profile_document,
    load_provider_role_profile,
    provider_role_profile_report,
    write_provider_role_profile,
)


class ProviderRoleProfileTests(unittest.TestCase):
    def document(self, **kwargs):
        values = {"profile_id": "engineering-default", "model_id": "llama3.2:3b"}
        values.update(kwargs)
        return create_provider_role_profile_document(**values)

    def test_default_profile_is_complete_and_has_no_tool_authority(self):
        document = self.document()
        parsed = _parse_profile(document, Path("provider_roles.json"))
        self.assertEqual(set(parsed.roles), set(PROFILE_ROLES))
        self.assertTrue(all(not binding.allowed_tools for binding in parsed.roles.values()))
        self.assertTrue(all(binding.provider_id == "ollama" for binding in parsed.roles.values()))

    def test_partial_role_family_is_rejected(self):
        document = self.document()
        document["roles"].pop("backend_api")
        with self.assertRaisesRegex(ProviderRoleProfileError, "complete family"):
            _parse_profile(document, Path("provider_roles.json"))

    def test_mutable_latest_model_alias_is_rejected(self):
        with self.assertRaisesRegex(ProviderRoleProfileError, "pinned model"):
            self.document(model_id="llama3.2:latest")

    def test_unknown_fields_and_duplicate_role_tools_are_rejected(self):
        document = self.document()
        document["unexpected"] = True
        with self.assertRaisesRegex(ProviderRoleProfileError, "fields"):
            _parse_profile(document, Path("provider_roles.json"))

        document = self.document()
        document["roles"]["backend_api"]["allowed_tools"] = ["read_ticket", "read_ticket"]
        with self.assertRaisesRegex(ProviderRoleProfileError, "unique list"):
            _parse_profile(document, Path("provider_roles.json"))

    def test_duplicate_json_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profile.json"
            path.write_text('{"schema_version":"x","schema_version":"y"}', encoding="utf-8")
            with self.assertRaisesRegex(ProviderRoleProfileError, "duplicate"):
                load_provider_role_profile(path)

    def test_profile_preview_is_read_only_and_apply_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".auren" / "provider_roles.json"
            preview = write_provider_role_profile(path, self.document(), apply=False)
            self.assertFalse(preview["applied"])
            self.assertFalse(path.exists())

            created = write_provider_role_profile(path, self.document(), apply=True)
            self.assertTrue(created["applied"])
            loaded = load_provider_role_profile(path)
            self.assertEqual(loaded.digest, created["profile_digest"])
            with self.assertRaisesRegex(ProviderRoleProfileError, "exists"):
                write_provider_role_profile(path, self.document(), apply=True)

    def test_profile_report_separates_policy_from_live_acceptance(self):
        document = self.document(provider_id="openai", local_model_id="llama3.2:3b")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "provider_roles.json"
            write_provider_role_profile(path, document, apply=True)
            profile = load_provider_role_profile(path)
            report = provider_role_profile_report(profile, environ={})
        self.assertTrue(report["configuration_ready"])
        self.assertTrue(report["route_policy_ready"])
        self.assertEqual(report["live_activation"], "not_performed")
        self.assertTrue(all(item["local_fallback_configured"] for item in report["roles"]))
        self.assertTrue(all(item["authenticated"] == "not_tested" for item in report["roles"]))

    def test_remote_only_profile_is_not_ready_without_explicit_policy(self):
        document = self.document(provider_id="openai")
        environment = {
            "OPENAI_API_KEY": "test-secret",
            "OPENAI_MODEL": "gpt-test-pinned",
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "provider_roles.json"
            write_provider_role_profile(path, document, apply=True)
            profile = load_provider_role_profile(path)
            report = provider_role_profile_report(profile, environ=environment)
        self.assertTrue(report["configuration_ready"])
        self.assertFalse(report["route_policy_ready"])
        self.assertFalse(report["ready_for_live_validation"])

    def test_remote_invoker_fails_closed_or_uses_local_fallback(self):
        binding = _parse_profile(
            self.document(provider_id="openai", local_model_id="llama3.2:3b"),
            Path("provider_roles.json"),
        ).roles["backend_api"]
        with mock.patch(
            "portable.provider_role_profiles._local_model_invoker",
            return_value=lambda _prompt, _timeout: "local-result",
        ), mock.patch(
            "portable.provider_role_profiles.OpenAICompatibleProvider.from_environment",
            side_effect=ValueError("missing key"),
        ):
            invoke = _role_invoker(binding, environ={})
            self.assertEqual(invoke("private task", 10), "local-result")

        remote_binding = _parse_profile(
            self.document(provider_id="openai"),
            Path("provider_roles.json"),
        ).roles["backend_api"]
        provider = mock.Mock()
        with mock.patch(
            "portable.provider_role_profiles.OpenAICompatibleProvider.from_environment",
            return_value=provider,
        ):
            invoke = _role_invoker(
                remote_binding,
                environ={
                    "OPENAI_API_KEY": "test-secret",
                    "OPENAI_MODEL": "gpt-test-pinned",
                },
            )
            prompt = "Return a proposal\n" + json.dumps({
                "task_request": {"privacy": "public"},
            })
            with self.assertRaises(PermissionError):
                invoke(prompt, 10)
        provider.complete.assert_not_called()

    def test_private_or_missing_privacy_is_never_classified_as_public(self):
        self.assertEqual(_prompt_privacy("no structured context"), "private")
        self.assertEqual(
            _prompt_privacy("prompt\n" + json.dumps({"task_request": {"privacy": "public"}})),
            "public",
        )
        self.assertEqual(
            _prompt_privacy("prompt\n" + json.dumps({"task_request": {"privacy": "restricted"}})),
            "private",
        )

    def test_profile_adapter_inputs_require_host_approved_tools(self):
        document = self.document()
        document["roles"]["backend_api"]["allowed_tools"] = ["read_ticket"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "provider_roles.json"
            write_provider_role_profile(path, document, apply=True)
            with self.assertRaisesRegex(ProviderRoleProfileError, "absent from the host-approved"):
                build_profiled_crew_adapter_inputs(path, tools=ApprovedToolRegistry())

    def test_supervised_runtime_registration_uses_complete_profile_and_canonical_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile_path = root / "provider_roles.json"
            write_provider_role_profile(profile_path, self.document(), apply=True)
            controller = type("ControllerStub", (), {})()
            controller.progress_journal = ProgressJournal(root / "progress.jsonl")
            with mock.patch(
                "portable.provider_role_profiles._local_model_invoker",
                return_value=lambda _prompt, _timeout: "local",
            ):
                adapters = runtime.register_supervised_profiled_crew_adapters(
                    controller,
                    profile_path,
                    tools=ApprovedToolRegistry(),
                    replace=True,
                )
        self.assertEqual(len(adapters), 14)
        self.assertIn("backend_api", adapters)
        self.assertIn("engineering_manager", adapters)

    def test_profile_adapter_inputs_cover_complete_role_families(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "provider_roles.json"
            write_provider_role_profile(path, self.document(), apply=True)
            with mock.patch(
                "portable.provider_role_profiles._local_model_invoker",
                return_value=lambda _prompt, _timeout: "local",
            ):
                result = build_profiled_crew_adapter_inputs(
                    path, tools=ApprovedToolRegistry(),
                )
        self.assertEqual(len(result["specialists"]), 8)
        self.assertEqual(len(result["personas"]), 6)
        self.assertEqual(
            {item["provider_id"] for item in result["specialists"].values()},
            {"ollama"},
        )


class IntegrationInventoryTests(unittest.TestCase):
    def test_jsonc_parser_preserves_comment_markers_inside_strings(self):
        raw = '''
        {
          // VS Code settings comment
          "mcp": {
            "servers": {
              "auren": {
                "command": "C:/AUREN/auren.exe",
                "note": "keep // and ,} inside a string",
              },
            },
          },
        }
        '''
        parsed = json.loads(_strip_jsonc(raw))
        self.assertEqual(parsed["mcp"]["servers"]["auren"]["note"], "keep // and ,} inside a string")

    def test_client_inventory_reads_jsonc_and_reports_config_without_claiming_live_test(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text(
                '{ // comment\n "mcp": {"servers": {"auren": {"command": "auren"}}}, }',
                encoding="utf-8",
            )
            result = _client_configuration({
                "name": "VS Code",
                "path": str(path),
                "key": "mcp.servers",
            })
        self.assertTrue(result["configured"])
        self.assertEqual(result["authenticated"], "not_tested")
        self.assertEqual(result["live_tested"], "not_tested")

    def test_remote_inventory_never_echoes_secrets_or_ignores_bad_provider_order(self):
        environment = {
            "AUREN_REMOTE_PROVIDER_ORDER": "openai,openai",
            "OPENAI_API_KEY": "do-not-print-this-secret",
            "OPENAI_MODEL": "gpt-test-pinned",
            "AUREN_LOCAL_REMOTE_INFERENCE": "enabled",
            "AUREN_LOCAL_DATA_CLASSIFICATION": "public",
        }
        report = _remote_provider_inventory(environment)
        encoded = json.dumps(report)
        self.assertNotIn("do-not-print-this-secret", encoded)
        self.assertTrue(report["configuration_errors"])
        openai = next(item for item in report["providers"] if item["id"] == "openai")
        self.assertFalse(openai["enabled"])

    def test_inventory_is_read_only_and_keeps_readiness_states_separate(self):
        from portable import onboarding

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            home = Path(temp) / "home"
            root.mkdir()
            home.mkdir()
            with mock.patch.object(onboarding, "doctor", return_value={
                "providers": [{"id": "codex", "command": "codex", "version": "1.0",
                               "capabilities": ["chat"], "status": "detected_not_live_validated"}],
                "local_model": {"available": False, "models": [], "status": "not_available"},
                "vibe_coding_host": {"status": "not_configured"},
            }), mock.patch.object(onboarding, "discover_clients", return_value=[]):
                report = collect_integration_inventory(
                    root,
                    home=home,
                    environ={
                        "AUREN_REMOTE_PROVIDER_ORDER": "openai",
                        "OPENAI_API_KEY": "secret-value",
                        "OPENAI_MODEL": "gpt-test-pinned",
                    },
                )
            self.assertEqual(
                report["discovery_policy"],
                "read_only_no_client_config_changes_no_remote_model_api_requests",
            )
            self.assertEqual(report["summary"]["mcp_hosts_detected"], 0)
            self.assertEqual(report["summary"]["provider_clis_detected"], 1)
            self.assertNotIn("secret-value", json.dumps(report))


class IntegrationsCliTests(unittest.TestCase):
    def test_profile_cli_preview_apply_and_validate(self):
        import auren_cli

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            preview_code = auren_cli._run_integrations([
                "--project-root", str(root),
                "profile", "init", "--model", "llama3.2:3b",
            ])
            self.assertEqual(preview_code, 0)
            self.assertFalse((root / ".auren" / "provider_roles.json").exists())

            apply_code = auren_cli._run_integrations([
                "--project-root", str(root),
                "profile", "init", "--model", "llama3.2:3b", "--apply",
            ])
            self.assertEqual(apply_code, 0)
            validate_code = auren_cli._run_integrations([
                "--project-root", str(root),
                "profile", "validate",
            ])
            self.assertEqual(validate_code, 0)


if __name__ == "__main__":
    unittest.main()
