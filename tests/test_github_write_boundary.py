import unittest
import json
import hashlib
import sys
import tempfile
from pathlib import Path
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


def _argument_digest(value):
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _schema_for(value):
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {key: _schema_for(item) for key, item in value.items()},
            "required": list(value),
            "additionalProperties": False,
        }
    if isinstance(value, list):
        return {"type": "array", "items": _schema_for(value[0]) if value else {"type": "string"}}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    return {"type": "string"}


def make_plan(**target_changes):
    target = {
        "repository": "acme/product",
        "branch": "feature/work",
        "base_sha": SHA,
        "allowed_paths": ["src/**", "tests/**"],
        "governance_repository": "acme/auren",
        "pull_request_base_branch": "main",
    }
    tool_arguments = {
        "create_branch": {"owner": "acme", "repo": "product", "branch": "feature/work", "sha": SHA},
        "push_files": {
            "owner": "acme", "repo": "product", "branch": "feature/work",
            "expected_head_sha": SHA, "files": [
                {"path": "src/main.py", "content": "value = 1"},
                {"path": "tests/test_main.py", "content": "assert True"},
            ],
        },
        "create_or_update_file": {
            "owner": "acme", "repo": "product", "branch": "feature/work",
            "expected_head_sha": SHA, "path": "src/main.py",
        },
        "create_commit": {
            "owner": "acme", "repo": "product", "branch": "feature/work",
            "expected_head_sha": SHA, "parents": [SHA],
            "files": [{"path": "src/main.py", "content": "value = 1"}],
        },
        "create_pull_request": {
            "owner": "acme", "repo": "product", "head": "feature/work", "base": "main",
            "expected_head_sha": HEAD, "expected_base_sha": SHA,
        },
        "merge_pull_request": {
            "owner": "acme", "repo": "product", "pull_number": 42,
            "head": "feature/work", "base": "main",
            "expected_head_sha": HEAD, "expected_base_sha": SHA,
        },
        "delete_branch": {
            "owner": "acme", "repo": "product", "branch": "feature/work",
            "expected_head_sha": SHA,
        },
    }
    tool_contract_overrides = target_changes.pop("tool_contract_overrides", {})
    tool_schema_overrides = target_changes.pop("tool_schema_overrides", {})
    if isinstance(tool_contract_overrides, dict):
        for name, arguments in tool_contract_overrides.items():
            tool_arguments[name] = arguments
    contracts = [
        {"name": name, "schema": tool_schema_overrides.get(name, _schema_for(arguments)), "arguments_digest": _argument_digest(arguments)}
        for name, arguments in tool_arguments.items()
    ]
    target.update(target_changes)
    return CanonicalExecutionPlan.from_mapping({
        "target": target,
        "requirements": ["Implement the requested product change."],
        "acceptance": ["All required checks pass on the exact candidate head."],
        "required_capabilities": ["workspace_filesystem", "github_mcp", "repository.write"],
        "capabilities": [
            {
                "name": "workspace_filesystem",
                "available": True,
                "evidence": "guarded filesystem host probe",
            },
            {
                "name": "github_mcp",
                "available": True,
                "evidence": "configured GitHub MCP server probe",
            },
            {
                "name": "repository.write",
                "available": True,
                "evidence": "authority-bound repository write adapter probe",
            },
        ],
        "stopping_condition": "Stop on any failed security or acceptance gate.",
        "tool_contracts": contracts,
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
        with self.assertRaises(GitHubWriteBoundaryError):
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
        bad_parent_plan = make_plan(tool_contract_overrides={"create_commit": args})
        with self.assertRaises(GitHubWriteBoundaryError):
            preflight_github_tool(
                argv=self.argv, tool_name="create_commit", arguments=args, plan=bad_parent_plan,
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
        bad_base_plan = make_plan(tool_contract_overrides={"create_pull_request": args})
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "base branch"):
            preflight_github_tool(
                argv=self.argv, tool_name="create_pull_request", arguments=args, plan=bad_base_plan,
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


    def test_canonical_plan_stores_argument_digest_not_file_content(self):
        payload = json.dumps(self.plan.payload(), sort_keys=True)
        self.assertNotIn("value = 1", payload)
        self.assertNotIn("assert True", payload)
        self.assertTrue(all("arguments_digest" in contract for contract in self.plan.payload()["tool_contracts"]))
        self.plan.validate_runtime(
            repository="acme/product", branch="feature/work", head_sha=SHA,
            host_capabilities={
                "workspace_filesystem": True, "github_mcp": True,
                "repository.write": True, "github_write_digest_contracts": True,
            },
        )

    def test_pull_request_base_branch_is_part_of_canonical_plan_digest(self):
        main_plan = make_plan(pull_request_base_branch="main")
        release_plan = make_plan(pull_request_base_branch="release")
        self.assertNotEqual(main_plan.digest, release_plan.digest)


    def test_tool_schema_must_require_expected_sha_and_file_paths(self):
        arguments = {
            "owner": "acme", "repo": "product", "branch": "feature/work",
            "expected_head_sha": SHA, "files": [
                {"path": "src/main.py", "content": "value = 1"},
                {"path": "tests/test_main.py", "content": "assert True"},
            ],
        }
        unsafe_schema = _schema_for(arguments)
        unsafe_schema["required"].remove("expected_head_sha")
        unsafe_plan = make_plan(tool_schema_overrides={"push_files": unsafe_schema})
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "expected-SHA"):
            preflight_github_tool(
                argv=self.argv, tool_name="push_files", arguments=arguments, plan=unsafe_plan,
            )

        unsafe_schema = _schema_for(arguments)
        unsafe_schema["properties"]["files"]["items"]["required"].remove("path")
        unsafe_plan = make_plan(tool_schema_overrides={"push_files": unsafe_schema})
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "require each file path"):
            preflight_github_tool(
                argv=self.argv, tool_name="push_files", arguments=arguments, plan=unsafe_plan,
            )

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
                "expected_head_sha": SHA, "files": [
                    {"path": "src/main.py", "content": "value = 1"},
                    {"path": "tests/test_main.py", "content": "assert True"},
                ],
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
        binding = SimpleNamespace(tool_name="push_files", arguments_digest=payload["tool_arguments_digest"])
        request = {"provider_operation": {"provider": "github"}}
        validate_github_write_authorization(
            payload, authorization=authorization, request=request, call_binding=binding,
        )

    def test_write_authorization_rejects_wrong_host_identity_and_missing_approval(self):
        payload = preflight_github_tool(
            argv=self.argv, tool_name="push_files",
            arguments={
                "owner": "acme", "repo": "product", "branch": "feature/work",
                "expected_head_sha": SHA, "files": [
                {"path": "src/main.py", "content": "value = 1"},
                {"path": "tests/test_main.py", "content": "assert True"},
            ],
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
                call_binding=SimpleNamespace(tool_name="push_files", arguments_digest=payload["tool_arguments_digest"]),
            )
        context.repository_slug = "acme/product"
        authorization.approval = None
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "trusted approval receipt"):
            validate_github_write_authorization(
                payload, authorization=authorization,
                request={"provider_operation": {"provider": "github"}},
                call_binding=SimpleNamespace(tool_name="push_files", arguments_digest=payload["tool_arguments_digest"]),
            )

    def test_write_authorization_requires_exact_action_target_and_evidence(self):
        payload = preflight_github_tool(
            argv=self.argv, tool_name="push_files",
            arguments={
                "owner": "acme", "repo": "product", "branch": "feature/work",
                "expected_head_sha": SHA, "files": [
                    {"path": "src/main.py", "content": "value = 1"},
                    {"path": "tests/test_main.py", "content": "assert True"},
                ],
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
                call_binding=SimpleNamespace(tool_name="push_files", arguments_digest=payload["tool_arguments_digest"]),
            )
        action_request.target = payload["target"]
        with self.assertRaisesRegex(GitHubWriteBoundaryError, "canonical plan and exact write target"):
            validate_github_write_authorization(
                payload, authorization=authorization,
                request={"provider_operation": {"provider": "github"}},
                call_binding=SimpleNamespace(tool_name="push_files", arguments_digest=payload["tool_arguments_digest"]),
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
            trusted_workspace_root=Path.cwd(),
        )
        request = {
            "owner": "acme", "repo": "product", "branch": "feature/work",
            "expected_head_sha": SHA, "files": [
                {"path": "src/main.py", "content": "value = 1"},
                {"path": "tests/test_main.py", "content": "assert True"},
            ],
        }
        with patch("subprocess.Popen", side_effect=AssertionError("child process must not start")):
            with self.assertRaises(AdapterUnavailable):
                adapter._call_untraced(request)



    def test_mcp_adapter_discovers_and_compares_live_github_tool_schema_before_call(self):
        from portable.vibe_coding_host import AdapterUnavailable, MCPStdioToolAdapter

        arguments = {
            "owner": "acme", "repo": "product", "branch": "feature/work",
            "expected_head_sha": SHA, "files": [
                {"path": "src/main.py", "content": "value = 1"},
                {"path": "tests/test_main.py", "content": "assert True"},
            ],
        }
        preflight = preflight_github_tool(
            argv=self.argv, tool_name="push_files", arguments=arguments, plan=self.plan,
        )
        schema_json = next(item[1] for item in self.plan.tool_contracts if item[0] == "push_files")
        schema = json.loads(schema_json)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "tool-called"
            server = root / "github_mcp_server.py"

            def write_server(live_schema):
                server.write_text(
                    "import json, pathlib, sys\n"
                    f"schema = json.loads({json.dumps(json.dumps(live_schema))})\n"
                    f"marker = {str(marker)!r}\n"
                    "for line in sys.stdin:\n"
                    "    request = json.loads(line)\n"
                    "    method = request.get('method')\n"
                    "    if method == 'initialize':\n"
                    "        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'fake-github','version':'1'}}}), flush=True)\n"
                    "    elif method == 'tools/list':\n"
                    "        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'tools':[{'name':'push_files','inputSchema':schema}]}}), flush=True)\n"
                    "    elif method == 'tools/call':\n"
                    "        pathlib.Path(marker).write_text('called')\n"
                    "        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'structuredContent':{'ok':True}}}), flush=True)\n"
                    "        break\n",
                    encoding="utf-8",
                )

            write_server(schema)
            adapter = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="push_files",
                argument_map={},
                timeout_seconds=5,
                requires_authority_binding=True,
                github_server=True,
                execution_plan=self.plan,
                trusted_workspace_root=root,
            )
            result = adapter._invoke_unchecked(
                {"state": {"workspace_root": str(root), "project_root": str(root)}},
                arguments=arguments,
                github_write_preflight=preflight,
            )
            self.assertEqual(result, {"ok": True})
            self.assertTrue(marker.is_file())

            marker.unlink()
            changed_schema = dict(schema)
            changed_schema["description"] = "different live schema"
            write_server(changed_schema)
            with self.assertRaisesRegex(AdapterUnavailable, "schema differs"):
                adapter._invoke_unchecked(
                    {"state": {"workspace_root": str(root), "project_root": str(root)}},
                    arguments=arguments,
                    github_write_preflight=preflight,
                )
            self.assertFalse(marker.exists())



if __name__ == "__main__":
    unittest.main()
