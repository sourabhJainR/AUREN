from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portable.agent_profiles import (
    AgentProfileError,
    load_agent_profile_registry,
    resolve_agent_profile,
    resolve_team_template,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "config" / "agent_profiles.v1.json"
ROLE_PATH = ROOT / "config" / "agent_org.v1.json"


class AgentProfileRegistryTests(unittest.TestCase):
    def test_builtin_profiles_reference_canonical_roles_without_expanding_actions(self):
        registry, registry_digest = load_agent_profile_registry(
            PROFILE_PATH, organization_registry_path=ROLE_PATH,
        )
        self.assertEqual("auren-agent-profiles-v1", registry["schema_version"])
        self.assertEqual(64, len(registry_digest))
        profile = resolve_agent_profile(
            "backend_implementer", revision=1,
            profile_path=PROFILE_PATH, organization_registry_path=ROLE_PATH,
        )
        self.assertEqual("BE-01", profile.role_id)
        self.assertEqual(("edit_assigned_paths", "run_scoped_tests", "propose_changes"), profile.action_classes)
        self.assertEqual("agent-profile:backend_implementer", profile.memory_namespace)
        self.assertEqual(64, len(profile.digest))

    def test_unknown_profile_and_stale_revision_fail_closed(self):
        with self.assertRaises(AgentProfileError):
            resolve_agent_profile("unknown", revision=1, profile_path=PROFILE_PATH, organization_registry_path=ROLE_PATH)
        with self.assertRaises(AgentProfileError):
            resolve_agent_profile("backend_implementer", revision=2, profile_path=PROFILE_PATH, organization_registry_path=ROLE_PATH)

    def test_team_template_resolves_profiles_and_dependency_order(self):
        template = resolve_team_template(
            "feature_delivery", revision=1,
            profile_path=PROFILE_PATH, organization_registry_path=ROLE_PATH,
        )
        self.assertEqual(("architect", "implementer", "qa", "security_review"), tuple(
            member["member_id"] for member in template["members"]
        ))
        self.assertEqual(64, len(template["digest"]))

    def test_role_action_expansion_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profiles.json"
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            data["profiles"][0]["action_classes"].append("bypass_security_or_release_gates")
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(AgentProfileError, "exceeds canonical role"):
                load_agent_profile_registry(path, organization_registry_path=ROLE_PATH)

    def test_unknown_role_and_manager_mismatch_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profiles.json"
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            data["profiles"][0]["role_id"] = "UNKNOWN-ROLE"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(AgentProfileError, "unknown canonical role"):
                load_agent_profile_registry(path, organization_registry_path=ROLE_PATH)

    def test_invalid_budget_and_unbounded_deadline_are_rejected(self):
        for field, value in (("budget_defaults", {"max_tokens": -1, "max_tool_calls": 1, "max_wall_seconds": 1}),
                             ("deadline_seconds", 90000)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "profiles.json"
                data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
                data["profiles"][0][field] = value
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(AgentProfileError):
                    load_agent_profile_registry(path, organization_registry_path=ROLE_PATH)

    def test_template_unknown_profile_and_dependency_cycle_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profiles.json"
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            data["team_templates"][0]["members"][0]["profile_id"] = "missing"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(AgentProfileError, "unknown profile"):
                load_agent_profile_registry(path, organization_registry_path=ROLE_PATH)

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profiles.json"
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            data["team_templates"][0]["members"][0]["depends_on"] = ["implementer"]
            data["team_templates"][0]["members"][1]["depends_on"] = ["architect"]
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(AgentProfileError, "dependency cycle"):
                load_agent_profile_registry(path, organization_registry_path=ROLE_PATH)

    def test_duplicate_json_keys_and_duplicate_profile_revisions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profiles.json"
            path.write_text('{"schema_version":"auren-agent-profiles-v1","schema_version":"bad","profiles":[],"team_templates":[]}', encoding="utf-8")
            with self.assertRaises(AgentProfileError):
                load_agent_profile_registry(path, organization_registry_path=ROLE_PATH)

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profiles.json"
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            data["profiles"].append(dict(data["profiles"][0]))
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(AgentProfileError, "duplicate profile"):
                load_agent_profile_registry(path, organization_registry_path=ROLE_PATH)

    def test_profile_digest_is_stable_and_provider_tool_preferences_are_not_grants(self):
        first, digest_one = load_agent_profile_registry(PROFILE_PATH, organization_registry_path=ROLE_PATH)
        second, digest_two = load_agent_profile_registry(PROFILE_PATH, organization_registry_path=ROLE_PATH)
        self.assertEqual(digest_one, digest_two)
        profile = resolve_agent_profile("backend_implementer", revision=1, profile_path=PROFILE_PATH, organization_registry_path=ROLE_PATH)
        self.assertTrue(profile.provider_preferences)
        self.assertTrue(profile.tool_preferences)
        self.assertIn("routing hints only", profile.as_dict()["authority_note"])


if __name__ == "__main__":
    unittest.main()
