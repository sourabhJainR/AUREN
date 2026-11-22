import unittest

from portable.repository_execution_preflight import (
    AcceptanceMatrix, AcceptanceRow, AcceptanceStatus, CapabilityCheck,
    RepositoryTarget, ToolContractError, capability_blockers,
    exact_head_ci_gate, validate_tool_arguments,
)

SHA = "a" * 40


class RepositoryExecutionPreflightTests(unittest.TestCase):
    def target(self, **changes):
        values = dict(repository="acme/product", branch="work/feature",
                      base_sha=SHA, allowed_paths=("src", "tests/**"),
                      governance_repository="acme/auren")
        values.update(changes)
        return RepositoryTarget(**values)

    def test_target_separates_governance_and_product_repositories(self):
        self.target().validate_observed_target(repository="acme/product", branch="work/feature", base_sha=SHA)
        with self.assertRaises(PermissionError):
            self.target().validate_observed_target(repository="acme/auren", branch="work/feature", base_sha=SHA)

    def test_governance_repository_requires_explicit_opt_in(self):
        with self.assertRaises(PermissionError):
            self.target(repository="acme/auren")
        self.target(repository="acme/auren", allow_governance_repository_changes=True)

    def test_path_scope_blocks_traversal_and_out_of_scope_writes(self):
        self.assertEqual(self.target().authorize_path("src/module.py"), "src/module.py")
        with self.assertRaises(ValueError):
            self.target().authorize_path("../secrets")
        with self.assertRaises(PermissionError):
            self.target().authorize_path("docs/unrelated.md")

    def test_base_sha_must_be_full(self):
        with self.assertRaises(ValueError):
            self.target(base_sha="abc")

    def test_tool_schema_checks_required_extra_enum_type_and_bounds(self):
        schema = {"type": "object", "required": ["branch"], "additionalProperties": False,
                  "properties": {"branch": {"type": "string", "minLength": 2},
                                 "mode": {"type": "string", "enum": ["read", "write"]},
                                 "count": {"type": "integer", "minimum": 1, "maximum": 3}}}
        validate_tool_arguments(schema, {"branch": "main", "mode": "read", "count": 2})
        for args in ({"mode": "read"}, {"branch": "x", "mode": "admin"},
                     {"branch": "main", "extra": True}, {"branch": "main", "count": True},
                     {"branch": "main", "count": 4}):
            with self.subTest(args=args), self.assertRaises(ToolContractError):
                validate_tool_arguments(schema, args)

    def test_unknown_schema_type_fails_closed(self):
        with self.assertRaises(ToolContractError):
            validate_tool_arguments({"type": "object", "properties": {"x": {"type": "file"}}}, {"x": "a"})

    def test_capability_detection_does_not_treat_missing_as_available(self):
        checks = [CapabilityCheck("python", True, "python --version", "2026-11-22")]
        self.assertEqual(capability_blockers(checks, ("python", "browser")),
                         ("browser: unavailable or not evidenced",))

    def test_passed_acceptance_requires_evidence_on_exact_head(self):
        row = AcceptanceRow("REQ-1", "Requirement", "src/a.py", "test_positive", "test_negative",
                            "browser_test", AcceptanceStatus.PASSED, "urn:test:1", SHA)
        matrix = AcceptanceMatrix("acme/product", SHA, (row,))
        self.assertEqual(matrix.status, "passed")
        self.assertEqual(len(matrix.digest), 64)
        with self.assertRaises(ValueError):
            AcceptanceMatrix("acme/product", SHA, (AcceptanceRow(
                "REQ-1", "Requirement", "src/a.py", "test_positive", "test_negative",
                "browser_test", AcceptanceStatus.PASSED, "urn:test:1", "b" * 40),))

    def test_incomplete_matrix_never_reports_pass(self):
        row = AcceptanceRow("REQ-1", "Requirement", "", "", "", "",
                            AcceptanceStatus.BLOCKED, residual_risk="browser runner unavailable")
        self.assertEqual(AcceptanceMatrix("acme/product", SHA, (row,)).status, "blocked")

    def test_ci_gate_requires_current_head_and_required_checks(self):
        checks = [
            {"name": "unit", "head_sha": SHA, "conclusion": "success", "required": True},
            {"name": "browser", "head_sha": SHA, "conclusion": "skipped", "required": True},
            {"name": "docs", "head_sha": SHA, "conclusion": "skipped", "required": False},
        ]
        result = exact_head_ci_gate(SHA, checks, required_check_names=("unit", "browser"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["checks"][1]["state"], "skipped_required")
        checks[1]["conclusion"] = "success"
        self.assertTrue(exact_head_ci_gate(SHA, checks)["passed"])
        checks[0]["head_sha"] = "b" * 40
        self.assertFalse(exact_head_ci_gate(SHA, checks)["passed"])
        checks[0]["head_sha"] = SHA
        checks[0]["conclusion"] = "neutral"
        self.assertFalse(exact_head_ci_gate(SHA, checks)["passed"])
        checks[0]["conclusion"] = "success"
        missing = exact_head_ci_gate(SHA, checks, required_check_names=("unit", "browser", "security"))
        self.assertFalse(missing["passed"])
        self.assertEqual(missing["missing_required"], ["security"])

    def test_acceptance_matrix_persists_via_canonical_ledger_contract(self):
        class Ledger:
            def record(self, **values):
                return values
        row = AcceptanceRow("REQ-1", "Requirement", "src/a.py", "test_positive", "test_negative",
                            "integration_test", AcceptanceStatus.PASSED, "urn:test:1", SHA)
        matrix = AcceptanceMatrix("acme/product", SHA, (row,))
        record = matrix.persist(Ledger(), signer_id="local-operator")
        self.assertEqual(record["evidence_type"], "acceptance_matrix")
        self.assertEqual(record["head_sha"], SHA)
        self.assertEqual(record["status"], "passed")


if __name__ == "__main__":
    unittest.main()
