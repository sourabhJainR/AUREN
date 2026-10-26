import unittest
import tomllib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

import extension_registry  # noqa: E402


class ExtensionRegistryTests(unittest.TestCase):
    def test_registry_is_present(self):
        self.assertTrue(extension_registry.REGISTRY.exists())

    def test_detection_is_read_only_and_structured(self):
        result = extension_registry.detect_extensions()
        self.assertIn("graphify", result)
        self.assertIn("code_memory", result)
        self.assertNotIn("auren", result)
        self.assertIn("superpowers", result)
        self.assertIn("ponytail", result)
        self.assertIn("caveman", result)
        self.assertIn("discovered_skills", result)
        for name, value in result.items():
            self.assertIn("available", value)
            if name != "discovered_skills":
                self.assertTrue(value["manifest_valid"], value["manifest_errors"])
                self.assertEqual([], value["manifest_errors"])
            self.assertIn("capabilities", value)
            self.assertIn("policy", value)
            self.assertIn("health", value)
            self.assertIn("version", value)
            self.assertIn("cost_class", value)
            self.assertIn("trust_class", value)
            if name != "discovered_skills":
                self.assertIn(value["privacy_class"], {"local-only", "external", "unknown"})
            self.assertIn("evidence_types", value)
            self.assertIn("freshness", value)
            self.assertIn("artifact_fresh", value)
            self.assertIsInstance(value["artifact_fresh"], bool)
            self.assertIn("artifact_freshness_check", value)
            self.assertIsInstance(value["artifact_freshness_check"], dict)
            self.assertIn("failure_mode", value)
            self.assertIn("permissions", value)
            self.assertIn("side_effects", value)
            self.assertIn("requires_approval", value)
            self.assertIn("output_limit_chars", value)
            self.assertGreater(value["output_limit_chars"], 0)

    def test_manifest_schema_rejects_missing_approval_and_unbounded_output(self):
        data = tomllib.loads(extension_registry.REGISTRY.read_text(encoding="utf-8"))
        valid = extension_registry.validate_registry_manifest(data)
        self.assertTrue(all(not errors for errors in valid.values()), valid)

        malformed = tomllib.loads(extension_registry.REGISTRY.read_text(encoding="utf-8"))
        malformed["extensions"]["mcp"]["requires_approval"] = "false"
        malformed["extensions"]["mcp"]["output_limit_chars"] = 0
        errors = extension_registry.validate_registry_manifest(malformed)["mcp"]
        self.assertIn("requires_approval_must_be_boolean", errors)
        self.assertIn("output_limit_chars_out_of_range", errors)

        unsafe = tomllib.loads(extension_registry.REGISTRY.read_text(encoding="utf-8"))
        unsafe["extensions"]["mcp"]["requires_approval"] = False
        unsafe_errors = extension_registry.validate_registry_manifest(unsafe)["mcp"]
        self.assertIn("side_effects_require_approval", unsafe_errors)
        self.assertIn("external_data_use_requires_approval", unsafe_errors)

    def test_discovery_is_not_a_trust_or_health_claim(self):
        result = extension_registry.detect_extensions()
        for name, value in result.items():
            if name == "discovered_skills":
                continue
            self.assertIn(value["trust_class"], {
                "unverified", "local-unverified", "external-unverified"
            })
            self.assertIn(value["health"], {"discovered", "unavailable"})
            self.assertEqual(value["requires_approval"], True)

    def test_metadata_defaults_fail_conservatively(self):
        result = extension_registry.detect_extensions()
        self.assertEqual(result["mcp"]["trust_class"], "external-unverified")
        self.assertEqual(result["mcp"]["requires_approval"], True)
        self.assertIn("ambiguous", result["mcp"]["failure_mode"])
        self.assertEqual(result["generic_skill"]["version"], "unknown")
        self.assertEqual(result["generic_skill"]["cost_class"], "unknown")

    def test_legacy_product_name_is_not_a_core_extension(self):
        entry = extension_registry.REGISTRY.read_text(encoding="utf-8")
        self.assertNotIn("[extensions.aer]", entry)
        self.assertNotIn("github.com/sourabhJainR/AUREN", entry)

    def test_skill_discovery_is_read_only(self):
        skills = extension_registry.discover_skills()
        self.assertIsInstance(skills, list)
        for skill in skills:
            self.assertIn("name", skill)
            self.assertIn("description", skill)
            self.assertIn("path", skill)
            self.assertEqual(skill["trust_class"], "unverified")
            self.assertEqual(skill["health"], "discovered")


if __name__ == "__main__":
    unittest.main()
