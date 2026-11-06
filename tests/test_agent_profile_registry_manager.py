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
    profile_binding,
    validate_profile_binding,
)
from portable.agent_profile_registry_manager import AgentProfileRegistryManager
from portable.orchestration import Graph, Node, NodeKind, Orchestrator, RunStatus


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "config" / "agent_profiles.v1.json"
ROLE_PATH = ROOT / "config" / "agent_org.v1.json"


class AgentProfileRegistryManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "profiles.json"
        self.path.write_bytes(PROFILE_PATH.read_bytes())
        self.manager = AgentProfileRegistryManager(
            self.path, organization_registry_path=ROLE_PATH,
        )

    def _raw(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    @staticmethod
    def _add_profile_revision(raw, *, revision=2):
        profile = dict(raw["profiles"][0])
        profile["revision"] = revision
        profile["memory_namespace"] = f"agent-profile:{profile['profile_id']}:r{revision}"
        profile["budget_defaults"] = dict(profile["budget_defaults"])
        profile["budget_defaults"]["max_tokens"] += 1000
        raw["profiles"].append(profile)
        return raw

    def test_preview_and_diff_are_read_only_and_source_bound(self):
        raw = self._add_profile_revision(self._raw())
        before = self.path.read_bytes()
        preview = self.manager.preview(raw)
        diff = self.manager.diff(raw)
        self.assertEqual("valid", preview["status"])
        self.assertEqual(preview["profiles"], diff["profiles"])
        self.assertEqual(
            [f"{raw['profiles'][0]['profile_id']}/2"],
            preview["profiles"]["added"],
        )
        self.assertEqual(before, self.path.read_bytes())

    def test_export_digest_matches_normalized_registry_identity(self):
        exported = self.manager.export()
        _normalized, digest = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )
        self.assertEqual(digest, exported["digest"])
        self.assertEqual(
            json.loads(exported["content"]),
            self._raw(),
        )
        preview = self.manager.preview(exported["content"])
        self.assertEqual("valid", preview["status"])
        self.assertEqual(exported["digest"], preview["current_digest"])
        unchanged = self.manager.import_registry(
            exported["content"], expected_current_digest=exported["digest"],
        )
        self.assertEqual("unchanged", unchanged["status"])
        self.assertEqual(64, len(exported["source_digest"]))

    def test_import_is_additive_and_identical_reimport_is_idempotent(self):
        raw = self._add_profile_revision(self._raw())
        _normalized, before = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )
        first = self.manager.import_registry(raw, expected_current_digest=before)
        self.assertEqual("imported", first["status"])
        self.assertEqual(["backend_implementer/2"], first["added_profiles"])
        self.assertEqual("import", first["receipt"]["operation"])
        self.assertEqual(before, first["receipt"]["previous_registry_digest"])
        self.assertEqual(first["registry_digest"], first["receipt"]["registry_digest"])
        self.assertEqual(64, len(first["receipt"]["receipt_digest"]))
        _normalized, after = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )
        second = self.manager.import_registry(raw, expected_current_digest=after)
        self.assertEqual("unchanged", second["status"])
        self.assertEqual(after, second["registry_digest"])
        self.assertEqual(2, len([
            item for item in _normalized["profiles"]
            if item["profile_id"] == "backend_implementer"
        ]))

    def test_conflicting_existing_revision_cannot_be_overwritten(self):
        raw = self._raw()
        raw["profiles"][0]["budget_defaults"]["max_tokens"] += 1
        _normalized, digest = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )
        with self.assertRaisesRegex(AgentProfileError, "cannot be overwritten"):
            self.manager.import_registry(raw, expected_current_digest=digest)
        self.assertEqual(raw["profiles"][0]["budget_defaults"]["max_tokens"] - 1,
                         self._raw()["profiles"][0]["budget_defaults"]["max_tokens"])

    def test_import_rejects_stale_digest_and_historical_revision_removal(self):
        raw = self._add_profile_revision(self._raw())
        _normalized, original_digest = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )
        self.manager.import_registry(raw, expected_current_digest=original_digest)
        with self.assertRaisesRegex(AgentProfileError, "changed since preview"):
            self.manager.import_registry(raw, expected_current_digest=original_digest)
        current_digest = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )[1]
        with self.assertRaisesRegex(AgentProfileError, "cannot be removed"):
            self.manager.import_registry(self._raw_without_revision_one(), expected_current_digest=current_digest)

    def _raw_without_revision_one(self):
        raw = self._raw()
        raw["profiles"] = [
            item for item in raw["profiles"]
            if not (item["profile_id"] == "backend_implementer" and item["revision"] == 2)
        ]
        return raw

    def test_profile_rollback_creates_new_revision_and_preserves_history(self):
        raw = self._add_profile_revision(self._raw())
        digest = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )[1]
        self.manager.import_registry(raw, expected_current_digest=digest)
        current_digest = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )[1]
        result = self.manager.rollback_profile(
            "backend_implementer", to_revision=1,
            expected_current_digest=current_digest,
        )
        self.assertEqual("rolled_back_as_new_revision", result["status"])
        self.assertEqual(3, result["new_revision"])
        old = resolve_agent_profile("backend_implementer", revision=1, profile_path=self.path, organization_registry_path=ROLE_PATH)
        revised = resolve_agent_profile("backend_implementer", revision=2, profile_path=self.path, organization_registry_path=ROLE_PATH)
        restored = resolve_agent_profile("backend_implementer", revision=3, profile_path=self.path, organization_registry_path=ROLE_PATH)
        self.assertEqual(old.action_classes, restored.action_classes)
        pinned = validate_profile_binding(
            profile_binding(old),
            role_id=old.role_id,
            manager_role_id=old.manager_role_id,
            action_class=old.action_classes[0],
            profile_path=self.path,
            organization_registry_path=ROLE_PATH,
        )
        self.assertEqual(old.digest, pinned.digest)
        self.assertNotEqual(old.memory_namespace, restored.memory_namespace)
        self.assertNotEqual(old.digest, revised.digest)
        self.assertEqual(3, result["historical_revision_count"])
        self.assertEqual("rollback_profile", result["receipt"]["operation"])
        self.assertEqual(64, len(result["receipt"]["receipt_digest"]))

    def test_template_rollback_creates_new_revision_without_rewriting_old_templates(self):
        raw = self._raw()
        template = dict(raw["team_templates"][0])
        template["revision"] = 2
        template["description"] = template["description"] + " revised"
        raw["team_templates"].append(template)
        digest = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )[1]
        self.manager.import_registry(raw, expected_current_digest=digest)
        current_digest = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )[1]
        result = self.manager.rollback_team_template(
            template["template_id"], to_revision=1,
            expected_current_digest=current_digest,
        )
        self.assertEqual(3, result["new_revision"])
        old = resolve_team_template(template["template_id"], revision=1, profile_path=self.path, organization_registry_path=ROLE_PATH)
        revised = resolve_team_template(template["template_id"], revision=2, profile_path=self.path, organization_registry_path=ROLE_PATH)
        restored = resolve_team_template(template["template_id"], revision=3, profile_path=self.path, organization_registry_path=ROLE_PATH)
        self.assertEqual(old["members"], restored["members"])
        self.assertNotEqual(old["description"], revised["description"])
        self.assertEqual(3, result["historical_revision_count"])
        self.assertEqual("rollback_team_template", result["receipt"]["operation"])
        self.assertEqual(64, len(result["receipt"]["receipt_digest"]))

    def test_preview_rejects_action_scope_widening(self):
        raw = self._raw()
        raw["profiles"][0]["action_classes"].append("deploy_production")
        with self.assertRaisesRegex(AgentProfileError, "exceeds canonical role authority"):
            self.manager.preview(raw)

    def test_verified_registry_change_receipt_is_persisted_in_run_evidence(self):
        raw = self._add_profile_revision(self._raw())
        digest = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )[1]
        result = self.manager.import_registry(raw, expected_current_digest=digest)
        receipt = result["receipt"]
        calls = []

        def verify(current_receipt):
            return (
                current_receipt["registry_digest"]
                == load_agent_profile_registry(
                    self.path, organization_registry_path=ROLE_PATH,
                )[1]
            )

        graph = Graph([
            Node("work", NodeKind.DETERMINISTIC, lambda _state: calls.append("ran") or {"ok": True}),
        ])
        run = Orchestrator(
            graph, profile_registry_receipt_verifier=verify,
        ).run(
            "profile-change-evidence",
            "Run the approved profile update.",
            context={"agent_profile_registry_change_receipts": [receipt]},
        )
        self.assertEqual(RunStatus.ACCEPTED, run.status)
        self.assertEqual(["ran"], calls)
        evidence = next(
            item for item in run.evidence
            if item.kind == "agent-profile-registry-change"
        )
        self.assertIn(receipt["receipt_digest"], evidence.summary)

    def test_unverified_or_forged_registry_change_receipt_blocks_before_execution(self):
        raw = self._add_profile_revision(self._raw())
        digest = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )[1]
        receipt = self.manager.import_registry(
            raw, expected_current_digest=digest,
        )["receipt"]
        forged = dict(receipt)
        forged["affected_revisions"] = ["backend_implementer/999"]
        calls = []
        graph = Graph([
            Node("work", NodeKind.DETERMINISTIC, lambda _state: calls.append("ran") or {"ok": True}),
        ])
        run = Orchestrator(
            graph, profile_registry_receipt_verifier=lambda _receipt: True,
        ).run(
            "profile-change-forged-evidence",
            "Run the approved profile update.",
            context={"agent_profile_registry_change_receipts": [forged]},
        )
        self.assertEqual(RunStatus.BLOCKED, run.status)
        self.assertEqual([], calls)
        self.assertIn("receipt verification failed", run.stop_reason)

        missing_verifier = Orchestrator(graph).run(
            "profile-change-no-verifier",
            "Run the approved profile update.",
            context={"agent_profile_registry_change_receipts": [receipt]},
        )
        self.assertEqual(RunStatus.BLOCKED, missing_verifier.status)
        self.assertEqual([], calls)

    def test_two_managers_cannot_overwrite_each_other_from_the_same_digest(self):
        raw_one = self._add_profile_revision(self._raw(), revision=2)
        raw_two = self._add_profile_revision(self._raw(), revision=3)
        digest = load_agent_profile_registry(
            self.path, organization_registry_path=ROLE_PATH,
        )[1]
        self.manager.import_registry(raw_one, expected_current_digest=digest)
        other = AgentProfileRegistryManager(self.path, organization_registry_path=ROLE_PATH)
        with self.assertRaisesRegex(AgentProfileError, "changed since preview"):
            other.import_registry(raw_two, expected_current_digest=digest)


if __name__ == "__main__":
    unittest.main()
