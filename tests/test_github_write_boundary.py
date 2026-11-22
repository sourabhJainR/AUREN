import unittest
from types import SimpleNamespace
from unittest.mock import patch

from portable.canonical_execution_plan import CanonicalExecutionPlan
from portable.github_write_boundary import (
    GitHubWriteBoundaryError,
    preflight_github_tool,
    validate_preflight_payload,
    validate_github_write_authorization,
)

SHA = "a" * 40
HEAD = "b" * 40


def make_plan(**target_changes):
    target = {
        "repository": "acme/product",
        "branch": "feature/work",
        "base_sha": SHA,
        "allowed_paths": ["src/**", "tests/**"],
        "governance_repository": "acme/auren",
        "pull_request_base_branch": "main",
    }
    target.update(target_changes)
    return CanonicalExecutionPlan.from_mapping({
        "target": target,
        "requirements": ["Implement the requested product change."],
        "acceptance": ["All required checks pass on the exact candidate head."],
        "required_capabilities": ["workspace_filesystem"],
        "capabilities": [{
            "name": "workspace_filesystem",
            "available": True,
            "evidence": "guarded filesystem host probe",
        }],
        "stopping_condition": "Stop on any failed security or acceptance gate.",
    })


class GitHubWriteBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.plan = make_plan()
        self.argv = ("npx", "-y", "@github/mcp-server")

    def test_push_files_binds_repository_branch_base_sha_and_every_path(self):
        payload = preflight_github_tool(
            argv=self.argv,
            tool_name="push_files",
            arguments={
                "owner": "acme",
                "repo": "product",
                "branch": "feature/work",
                "expected_head_sha": SHA,
                "files": [
                    {"path": "src/main.py", "content": "value = 1"},
                    {"path": "tests/test_main.py", "content": "assert True"},
                ],
            },
            plan=self.plan,
        )
        checked = validate_preflight_payload(payload)
        self.assertEqual(checked["repository"], "acme/product")
        self.assertEqual(checked["branch"], "feature/work")
        self.assertEqual(checked["base_sha"], SHA)
        self.assertEqual(checked["expected_head_sha"], SHA)
        self.assertEqual(checked["paths"], ["src/main.py", "tests/test_main.py"])
        self.assertEqual(checked["action"], "repository.write")
        self.assertEqual(checked["target"], "acme/product@feature/work")

    def test_create_branch_requires_exact_repository_branch_and_base_sha(self):
        payload = preflight_github_tool(
            argv=self.argv,
            tool_name="create_branch",
            arguments={"owner": "acme", "repo": "product", "branch": "feature/work", "sha": SHA},
            plan=self.plan,
        )
        self.assertEqual(validate_preflight_payload(payload)["base_sha"], SHA)
        for args in (
            {"owner": "acme", "repo": "product", "branch": "feature/other", "sha": SHA},
            {"owner": "acme", "repo": "product", "branch": "feature/work", "sha": HEAD},
        ):
            with self.subTest(args=args), self.assertRaises(GitHubWriteBoundaryError):
                preflight_github_tool(
                    argv=self.argv, tool_name="create_branch", arguments=args, plan=self.plan,
                )

    def test_wrong_repository_and_wrong_branch_are_denied(self):
        base = {
            "owner": "acme", "repo": "product", "branch": "feature/work",
            "expected_head_sha": SHA, "path": "src/main.py",
        }
        for changes in (
            {"repo": "other"},
            {"branch": "main"},
        ):
            arguments = dict(base)
            arguments.update(changes)
            with self.subTest(arguments=arguments), self.assertRaises(GitHubWriteBoundaryError):
                preflight_github_tool(
                    argv=self.argv, tool_name="create_or_update_file",
                    arguments=arguments, plan=self.plan,
                )

    def test_stale_or_missing_expected_head_sha_is_denied(self):
        base = {
            "owner": "acme", "repo": "product", "branch": "feature/work",
            "path": "src/main.py",
        }
        for changes in ({"expected_head_sha": HEAD}, {}):
            arguments = dict(base)
            arguments.update(changes)
            with self.subTest(arguments=arguments), self.assertRaises(GitHubWriteBoundaryError):
                preflight_github_tool(
                    argv=self.argv, tool_name="create_or_update_file",
                    arguments=arguments, plan=self.plan,
                )

    def test_every_file_path_must_be_in_scope(self):
        for path in ("../secrets/token.txt", "docs/unrelated.md", "src/private/token.txt"):
            with self.subTest(path=path), self.assertRaises(GitHubWriteBoundaryError):
                preflight_github_tool(
                    argv=self.argv, tool_name="push_files",
                    arguments={
                        "owner": "acme", "repo": "product", "branch": "feature/work",
                        "expected_head_sha": SHA, "files": [{"path": path, "content": "x"}],
                    },
                    plan=self.plan,
                )

    def test_file_mutation_requires_complete_path_inventory(self):
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "every written path"):
            preflight_github_tool(
                argv=self.argv, tool_name="push_files",
                arguments={
                    "owner": "acme", "repo": "product", "branch": "feature/work",
                    "expected_head_sha": SHA, "files": [{"content": "missing path"}],
                },
                plan=self.plan,
            )

    def test_create_commit_requires_exactly_the_approved_parent(self):
        args = {
            "owner": "acme", "repo": "product", "branch": "feature/work",
            "expected_head_sha": SHA, "parents": [SHA],
            "files": [{"path": "src/main.py", "content": "value = 1"}],
        }
        self.assertEqual(
            validate_preflight_payload(preflight_github_tool(
                argv=self.argv, tool_name="create_commit", arguments=args, plan=self.plan,
            ))["base_sha"],
            SHA,
        )
        args["parents"] = [HEAD]
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "parent"):
            preflight_github_tool(
                argv=self.argv, tool_name="create_commit", arguments=args, plan=self.plan,
            )

    def test_pull_request_create_binds_head_base_and_both_shas(self):
        payload = preflight_github_tool(
            argv=self.argv,
            tool_name="create_pull_request",
            arguments={
                "owner": "acme", "repo": "product",
                "head": "feature/work", "base": "main",
                "expected_head_sha": HEAD, "expected_base_sha": SHA,
            },
            plan=self.plan,
        )
        checked = validate_preflight_payload(payload)
        self.assertEqual(checked["operation"], "create_pull_request")
        self.assertEqual(checked["expected_head_sha"], HEAD)
        self.assertEqual(checked["target"], f"acme/product#new@{HEAD}:feature/work->main@{SHA}")

    def test_pull_request_write_requires_plan_declared_base_branch_and_exact_sha(self):
        plan = make_plan(pull_request_base_branch="")
        args = {
            "owner": "acme", "repo": "product", "head": "feature/work", "base": "main",
            "expected_head_sha": HEAD, "expected_base_sha": SHA,
        }
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "pull_request_base_branch"):
            preflight_github_tool(
                argv=self.argv, tool_name="create_pull_request", arguments=args, plan=plan,
            )
        args["base"] = "release"
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "base branch"):
            preflight_github_tool(
                argv=self.argv, tool_name="create_pull_request", arguments=args, plan=self.plan,
            )
        args["base"] = "main"
        args.pop("expected_head_sha")
        with self.assertRaises(GitHubWriteBoundaryError):
            preflight_github_tool(
                argv=self.argv, tool_name="create_pull_request", arguments=args, plan=self.plan,
            )

    def test_pull_request_merge_requires_number_and_exact_head_sha(self):
        payload = preflight_github_tool(
            argv=self.argv,
            tool_name="merge_pull_request",
            arguments={
                "owner": "acme", "repo": "product", "pull_number": 42,
                "head": "feature/work", "base": "main",
                "expected_head_sha": HEAD, "expected_base_sha": SHA,
            },
            plan=self.plan,
        )
        checked = validate_preflight_payload(payload)
        self.assertEqual(checked["action"], "merge")
        self.assertEqual(checked["target"], f"acme/product#42@{HEAD}:feature/work->main@{SHA}")
        with self.assertRaises(GitHubWriteBoundaryError):
            preflight_github_tool(
                argv=self.argv, tool_name="merge_pull_request",
                arguments={
                    "owner": "acme", "repo": "product", "pull_number": 42,
                    "head": "feature/work", "base": "main", "expected_base_sha": SHA,
                },
                plan=self.plan,
            )

    def test_delete_branch_requires_exact_target_and_sha(self):
        payload = preflight_github_tool(
            argv=self.argv, tool_name="delete_branch",
            arguments={
                "owner": "acme", "repo": "product", "branch": "feature/work",
                "expected_head_sha": SHA,
            },
            plan=self.plan,
        )
        self.assertEqual(validate_preflight_payload(payload)["action"], "delete_remote")

    def test_unknown_github_tool_fails_closed_but_audited_read_is_allowed(self):
        self.assertIsNone(preflight_github_tool(
            argv=self.argv, tool_name="get_pull_request",
            arguments={"owner": "acme", "repo": "product", "pull_number": 42},
            plan=None,
        ))
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "not in the audited"):
            preflight_github_tool(
                argv=self.argv, tool_name="mystery_repository_action",
                arguments={"owner": "acme", "repo": "product"},
                plan=None,
            )

    def test_github_mutation_without_canonical_plan_is_denied(self):
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "active canonical"):
            preflight_github_tool(
                argv=self.argv, tool_name="create_branch",
                arguments={"owner": "acme", "repo": "product", "branch": "feature/work", "sha": SHA},
                plan=None,
            )

    def test_non_github_mcp_tools_are_not_misclassified(self):
        self.assertIsNone(preflight_github_tool(
            argv=("python", "custom_mcp.py"), tool_name="create_branch",
            arguments={"branch": "feature/work"}, plan=None,
        ))


    def test_pull_request_base_branch_is_part_of_canonical_plan_digest(self):
        main_plan = make_plan(pull_request_base_branch="main")
        release_plan = make_plan(pull_request_base_branch="release")
        self.assertNotEqual(main_plan.digest, release_plan.digest)

    def test_preflight_payload_detects_tampering(self):
        payload = preflight_github_tool(
            argv=self.argv, tool_name="create_branch",
            arguments={"owner": "acme", "repo": "product", "branch": "feature/work", "sha": SHA},
            plan=self.plan,
        )
        payload["branch"] = "main"
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "digest is invalid"):
            validate_preflight_payload(payload)


    def test_write_authorization_binds_host_identity_action_target_and_approval(self):
        payload = preflight_github_tool(
            argv=self.argv, tool_name="push_files",
            arguments={
                "owner": "acme", "repo": "product", "branch": "feature/work",
                "expected_head_sha": SHA, "files": [{"path": "src/main.py", "content": "x"}],
            },
            plan=self.plan,
        )
        digest = payload["digest"]
        action_request = SimpleNamespace(
            capability="repository.write",
            action="repository.write",
            target=payload["target"],
            risk="external_effect",
            idempotency_key="github-write-operation-1",
            evidence_refs=(f"github-write:{digest}", f"canonical-plan:{self.plan.digest}"),
        )

        class Gateway:
            def requires_approval(self, request):
                return True

            def evaluate(self, request, *, approval=None):
                return SimpleNamespace(allowed=approval == "approved", approval_required=True)

        authorization = SimpleNamespace(
            trusted_context=SimpleNamespace(
                repository_slug="acme/product",
                repository_branch="feature/work",
                repository_revision=SHA,
                execution_plan_digest=self.plan.digest,
            ),
            action_request=action_request,
            gateway=Gateway(),
            approval="approved",
        )
        binding = SimpleNamespace(tool_name="push_files")
        request = {"provider_operation": {"provider": "github"}}
        validate_github_write_authorization(
            payload, authorization=authorization, request=request, call_binding=binding,
        )

    def test_write_authorization_rejects_wrong_host_identity_and_missing_approval(self):
        payload = preflight_github_tool(
            argv=self.argv, tool_name="push_files",
            arguments={
                "owner": "acme", "repo": "product", "branch": "feature/work",
                "expected_head_sha": SHA, "files": [{"path": "src/main.py", "content": "x"}],
            },
            plan=self.plan,
        )
        action_request = SimpleNamespace(
            capability="repository.write", action="repository.write",
            target=payload["target"], risk="external_effect",
            idempotency_key="github-write-operation-1",
            evidence_refs=(f"github-write:{payload['digest']}", f"canonical-plan:{self.plan.digest}"),
        )

        class Gateway:
            def requires_approval(self, request):
                return True

            def evaluate(self, request, *, approval=None):
                return SimpleNamespace(allowed=approval == "approved", approval_required=True)

        context = SimpleNamespace(
            repository_slug="other/repo",
            repository_branch="feature/work",
            repository_revision=SHA,
            execution_plan_digest=self.plan.digest,
        )
        authorization = SimpleNamespace(
            trusted_context=context, action_request=action_request,
            gateway=Gateway(), approval="approved",
        )
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "host-verified repository"):
            validate_github_write_authorization(
                payload, authorization=authorization,
                request={"provider_operation": {"provider": "github"}},
                call_binding=SimpleNamespace(tool_name="push_files"),
            )
        context.repository_slug = "acme/product"
        authorization.approval = None
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "approval was not accepted"):
            validate_github_write_authorization(
                payload, authorization=authorization,
                request={"provider_operation": {"provider": "github"}},
                call_binding=SimpleNamespace(tool_name="push_files"),
            )

    def test_write_authorization_requires_exact_action_target_and_evidence(self):
        payload = preflight_github_tool(
            argv=self.argv, tool_name="push_files",
            arguments={
                "owner": "acme", "repo": "product", "branch": "feature/work",
                "expected_head_sha": SHA, "files": [{"path": "src/main.py", "content": "x"}],
            },
            plan=self.plan,
        )

        class Gateway:
            def requires_approval(self, request):
                return True

            def evaluate(self, request, *, approval=None):
                return SimpleNamespace(allowed=True, approval_required=True)

        context = SimpleNamespace(
            repository_slug="acme/product", repository_branch="feature/work",
            repository_revision=SHA, execution_plan_digest=self.plan.digest,
        )
        action_request = SimpleNamespace(
            capability="repository.write", action="repository.write",
            target="acme/product@main", risk="external_effect",
            idempotency_key="github-write-operation-1",
            evidence_refs=(f"github-write:{payload['digest']}",),
        )
        authorization = SimpleNamespace(
            trusted_context=context, action_request=action_request,
            gateway=Gateway(), approval="approved",
        )
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "approved repository/branch/PR"):
            validate_github_write_authorization(
                payload, authorization=authorization,
                request={"provider_operation": {"provider": "github"}},
                call_binding=SimpleNamespace(tool_name="push_files"),
            )
        action_request.target = payload["target"]
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "canonical plan and exact write target"):
            validate_github_write_authorization(
                payload, authorization=authorization,
                request={"provider_operation": {"provider": "github"}},
                call_binding=SimpleNamespace(tool_name="push_files"),
            )

    def test_mcp_adapter_blocks_github_write_before_child_process_without_authority(self):
        from portable.vibe_coding_host import AdapterUnavailable, MCPStdioToolAdapter

        adapter = MCPStdioToolAdapter(
            argv=("python", "fake-github-mcp-server"),
            tool_name="push_files",
            argument_map={
                "owner": "$owner", "repo": "$repo", "branch": "$branch",
                "expected_head_sha": "$expected_head_sha", "files": "$files",
            },
            timeout_seconds=1,
            requires_authority_binding=False,
            github_server=True,
            execution_plan=self.plan,
        )
        request = {
            "owner": "acme", "repo": "product", "branch": "feature/work",
            "expected_head_sha": SHA, "files": [{"path": "src/main.py", "content": "x"}],
        }
        with patch("subprocess.Popen", side_effect=AssertionError("child process must not start")):
            with self.assertRaises(AdapterUnavailable):
                adapter._call_untraced(request)



if __name__ == "__main__":
    unittest.main()
