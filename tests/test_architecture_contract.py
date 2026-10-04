"""Architecture constitution regression tests."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_architecture.py"
CONTRACT = ROOT / "architecture" / "architecture.yaml"


def load_contract() -> dict[str, object]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


class ArchitectureContractTests(unittest.TestCase):
    def test_contract_is_valid(self) -> None:
        namespace: dict[str, object] = {"__file__": str(VALIDATOR), "__name__": "auren_architecture_validator_test"}
        source = VALIDATOR.read_text(encoding="utf-8")
        exec(compile(source, str(VALIDATOR), "exec"), namespace)
        validate = namespace["validate"]
        self.assertTrue(callable(validate))
        self.assertIsNone(validate())

    def test_contract_is_yaml_compatible_json(self) -> None:
        self.assertEqual(load_contract()["architecture"]["short_name"], "AUREN")

    def test_enforcement_points_exist(self) -> None:
        contract = load_contract()
        for key in ("validator", "tests", "ci"):
            relative_path = contract["enforcement"][key]
            self.assertIsInstance(relative_path, str)
            self.assertTrue((ROOT / relative_path).is_file(), f"missing architecture enforcement point: {relative_path}")
        self.assertIs(contract["enforcement"]["hard_fail"], True)

    def test_learning_remains_advisory(self) -> None:
        contract = load_contract()
        rule = contract["domains"]["learning"]["rule"].lower()
        self.assertIn("cannot grant permissions", rule)
        self.assertIn("bypass verification", rule)

    def test_single_canonical_owner_per_domain(self) -> None:
        contract = load_contract()
        owners = [domain["canonical_owner"] for domain in contract["domains"].values()]
        self.assertEqual(len(owners), len(set(owners)))


if __name__ == "__main__":
    unittest.main()
