import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.vibe_coding_host import LocalVibeCodingHost, VibeHostConfig
from portable.repository_execution_preflight import (
    AcceptanceMatrix, AcceptanceRow, AcceptanceStatus, CapabilityCheck,
    RepositoryTarget, ToolContractError, capability_blockers,
    exact_head_ci_gate, validate_tool_arguments, guard_governance_repository,
    repository_slug_from_remote,
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

    def test_vibe_host_blocks_governance_repo_before_worktree_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = VibeHostConfig(
                provider=None, reviewer_provider=None, allow_workspace_writes=True,
                test_commands=(("python", "-c", "pass"),), mcp_tools={},
            )
            host = LocalVibeCodingHost(
                directory, config=config, state_root=Path(directory) / "state"
            )
            remote = subprocess.CompletedProcess(
                ["git", "remote", "get-url", "origin"], 0,
                "https://github.com/acme/auren.git", "",
            )
            with patch.dict(os.environ, {
                "AUREN_GOVERNANCE_REPOSITORY": "acme/auren",
                "AUREN_ALLOW_GOVERNANCE_REPOSITORY_CHANGES": "0",
            }):
                with patch("portable.vibe_coding_host._run", return_value=remote):
                    with self.assertRaisesRegex(PermissionError, "governance repository"):
                        host.run("guard-test", "implement product feature")

    def test_vibe_host_blocks_missing_or_unrecognized_origin_before_worktree_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = VibeHostConfig(
                provider=None, reviewer_provider=None, allow_workspace_writes=True,
                test_commands=(("python", "-c", "pass"),), mcp_tools={},
            )
            host = LocalVibeCodingHost(
                directory, config=config, state_root=Path(directory) / "state"
            )
            cases = (
                subprocess.CompletedProcess(
                    ["git", "remote", "get-url", "origin"], 2, "", "no origin"
                ),
                subprocess.CompletedProcess(
                    ["git", "remote", "get-url", "origin"], 0,
                    "ssh://git@gitlab.com/acme/product.git", "",
                ),
                subprocess.CompletedProcess(
                    ["git", "remote", "get-url", "origin"], 0,
                    "https://github.com/acme/product?ref=main", "",
                ),
            )
            for origin in cases:
                with self.subTest(origin=origin):
                    with patch.dict(os.environ, {
                        "AUREN_GOVERNANCE_REPOSITORY": "acme/auren",
                        "AUREN_ALLOW_UNVERIFIED_REPOSITORY_IDENTITY": "0",
                    }):
                        with patch("portable.vibe_coding_host._run", return_value=origin):
                            with patch("portable.vibe_coding_host.IsolatedGitWorktree.open") as open_worktree:
                                with self.assertRaisesRegex(PermissionError, "verify the implementation repository identity"):
                                    host.run("unverified-origin", "implement product feature")
                                open_worktree.assert_not_called()

    def test_unverified_origin_requires_a_separate_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            config = VibeHostConfig(
                provider=None, reviewer_provider=None, allow_workspace_writes=True,
                test_commands=(("python", "-c", "pass"),), mcp_tools={},
            )
            host = LocalVibeCodingHost(
                directory, config=config, state_root=Path(directory) / "state"
            )
            origin = subprocess.CompletedProcess(
                ["git", "remote", "get-url", "origin"], 2, "", "no origin"
            )
            with patch.dict(os.environ, {
                "AUREN_ALLOW_UNVERIFIED_REPOSITORY_IDENTITY": "1",
                "AUREN_IMPLEMENTATION_REPOSITORY": "acme/product",
            }):
                with patch("portable.vibe_coding_host._run", return_value=origin):
                    with patch(
                        "portable.vibe_coding_host.IsolatedGitWorktree.open",
                        side_effect=RuntimeError("worktree reached"),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "worktree reached"):
                            host.run("explicit-unverified-origin", "implement product feature")

    def test_unverified_identity_requires_a_declared_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            config = VibeHostConfig(
                provider=None, reviewer_provider=None, allow_workspace_writes=True,
                test_commands=(("python", "-c", "pass"),), mcp_tools={},
            )
            host = LocalVibeCodingHost(
                directory, config=config, state_root=Path(directory) / "state"
            )
            origin = subprocess.CompletedProcess(
                ["git", "remote", "get-url", "origin"], 2, "", "no origin"
            )
            with patch.dict(os.environ, {
                "AUREN_ALLOW_UNVERIFIED_REPOSITORY_IDENTITY": "1",
                "AUREN_IMPLEMENTATION_REPOSITORY": "",
            }):
                with patch("portable.vibe_coding_host._run", return_value=origin):
                    with patch("portable.vibe_coding_host.IsolatedGitWorktree.open") as open_worktree:
                        with self.assertRaisesRegex(PermissionError, "requires AUREN_IMPLEMENTATION_REPOSITORY"):
                            host.run("missing-declaration", "implement product feature")
                        open_worktree.assert_not_called()

    def test_unverified_declaration_does_not_bypass_governance_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            config = VibeHostConfig(
                provider=None, reviewer_provider=None, allow_workspace_writes=True,
                test_commands=(("python", "-c", "pass"),), mcp_tools={},
            )
            host = LocalVibeCodingHost(
                directory, config=config, state_root=Path(directory) / "state"
            )
            origin = subprocess.CompletedProcess(
                ["git", "remote", "get-url", "origin"], 2, "", "no origin"
            )
            with patch.dict(os.environ, {
                "AUREN_ALLOW_UNVERIFIED_REPOSITORY_IDENTITY": "1",
                "AUREN_IMPLEMENTATION_REPOSITORY": "acme/auren",
                "AUREN_GOVERNANCE_REPOSITORY": "acme/auren",
                "AUREN_ALLOW_GOVERNANCE_REPOSITORY_CHANGES": "0",
            }):
                with patch("portable.vibe_coding_host._run", return_value=origin):
                    with patch("portable.vibe_coding_host.IsolatedGitWorktree.open") as open_worktree:
                        with self.assertRaisesRegex(PermissionError, "governance repository"):
                            host.run("declared-governance-repository", "implement product feature")
                        open_worktree.assert_not_called()

    def test_governance_remote_guard_recognizes_https_and_ssh(self):
        for remote in ("https://github.com/acme/auren.git", "git@github.com:acme/auren.git"):
            with self.subTest(remote=remote), self.assertRaises(PermissionError):
                guard_governance_repository(remote, governance_repository="acme/auren")
            self.assertEqual(
                guard_governance_repository(
                    remote, governance_repository="acme/auren",
                    allow_governance_repository_changes=True,
                ),
                "acme/auren",
            )
        self.assertEqual(
            guard_governance_repository(
                "https://github.com/acme/product.git", governance_repository="acme/auren"
            ),
            "acme/product",
        )
        self.assertIsNone(
            repository_slug_from_remote("https://github.com/acme/product?ref=main")
        )
        with self.assertRaises(ValueError):
            guard_governance_repository(
                "https://github.com/acme/product.git", governance_repository="../auren"
            )

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

    def test_path_scope_globs_do_not_cross_directories(self):
        one_level = self.target(allowed_paths=("src/*.py",))
        self.assertEqual(one_level.authorize_path("src/main.py"), "src/main.py")
        with self.assertRaises(PermissionError):
            one_level.authorize_path("src/private/token.py")

        recursive = self.target(allowed_paths=("src/**/test_*.py",))
        self.assertEqual(recursive.authorize_path("src/test_unit.py"), "src/test_unit.py")
        self.assertEqual(
            recursive.authorize_path("src/private/test_unit.py"),
            "src/private/test_unit.py",
        )
        with self.assertRaises(PermissionError):
            recursive.authorize_path("src/private/token.py")

    def test_target_rejects_string_path_scope(self):
        with self.assertRaisesRegex(ValueError, "allowed_paths"):
            self.target(allowed_paths="src")

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

    def test_tool_schema_rejects_null_property_schemas(self):
        schema = {
            "type": "object",
            "properties": {"config": None},
            "additionalProperties": False,
        }
        with self.assertRaisesRegex(ToolContractError, "properties schema"):
            validate_tool_arguments(schema, {"config": {"branch": "main"}})

    def test_tool_schema_enum_uses_json_type_equality(self):
        invalid_schemas_and_arguments = (
            ({"type": "object", "properties": {"enabled": {
                "type": "boolean", "enum": [1],
            }}}, {"enabled": True}),
            ({"type": "object", "properties": {"count": {
                "type": "integer", "enum": [True],
            }}}, {"count": 1}),
            ({"type": "object", "properties": {"value": {
                "type": "object", "enum": [{"enabled": 1}],
            }}}, {"value": {"enabled": True}}),
        )
        for schema, arguments in invalid_schemas_and_arguments:
            with self.subTest(schema=schema), self.assertRaises(ToolContractError):
                validate_tool_arguments(schema, arguments)

    def test_nested_tool_schemas_validate_required_properties_and_array_items(self):
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "required": ["branch"],
                    "additionalProperties": False,
                    "properties": {"branch": {"type": "string", "minLength": 2}},
                },
                "targets": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "integer", "minimum": 1},
                },
            },
            "required": ["config", "targets"],
            "additionalProperties": False,
        }
        validate_tool_arguments(
            schema, {"config": {"branch": "main"}, "targets": [1, 2]}
        )
        invalid_arguments = (
            {"config": {}, "targets": [1]},
            {"config": {"branch": "main", "extra": True}, "targets": [1]},
            {"config": {"branch": "main"}, "targets": [1, "two"]},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(ToolContractError):
                validate_tool_arguments(schema, arguments)

    def test_unsupported_nested_schema_constraints_fail_closed(self):
        schema = {
            "type": "object",
            "properties": {"branch": {"type": "string", "pattern": "^[a-z]+$"}},
        }
        with self.assertRaisesRegex(ToolContractError, "unsupported schema keywords"):
            validate_tool_arguments(schema, {"branch": "main"})

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

    def test_acceptance_matrix_rejects_dot_segments_in_repository_slug(self):
        row = AcceptanceRow(
            "REQ-1", "Requirement", "", "", "", "",
            AcceptanceStatus.BLOCKED, residual_risk="not run",
        )
        for repository in ("../product", "./product", "owner/..", "owner/."):
            with self.subTest(repository=repository), self.assertRaisesRegex(
                ValueError, "canonical repository"
            ):
                AcceptanceMatrix(repository, SHA, (row,))

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
        self.assertTrue(exact_head_ci_gate(
            SHA, checks, required_check_names=("unit", "browser")
        )["passed"])
        checks[0]["head_sha"] = "b" * 40
        self.assertFalse(exact_head_ci_gate(
            SHA, checks, required_check_names=("unit", "browser")
        )["passed"])
        checks[0]["head_sha"] = SHA
        checks[0]["conclusion"] = "neutral"
        self.assertFalse(exact_head_ci_gate(
            SHA, checks, required_check_names=("unit", "browser")
        )["passed"])
        checks[0]["conclusion"] = "success"
        missing = exact_head_ci_gate(SHA, checks, required_check_names=("unit", "browser", "security"))
        self.assertFalse(missing["passed"])
        self.assertEqual(missing["missing_required"], ["security"])
        optionalized = [
            {"name": "required", "head_sha": SHA, "conclusion": "skipped", "required": False}
        ]
        self.assertFalse(exact_head_ci_gate(
            SHA, optionalized, required_check_names=("required",)
        )["passed"])
        with self.assertRaises(TypeError):
            exact_head_ci_gate(SHA, checks)

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
