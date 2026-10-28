from __future__ import annotations

from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.action_governance import ActionGovernanceGateway, ActionPolicy, ActionRequest
from portable.execution_boundary import ExecutionAuthorityBinding
from portable.runtime_recovery import RuntimeExecutionGuard
from portable.side_effect_ledger import DurableSideEffectLedger
from portable.vibe_coding_adapters import AdapterUnavailable
from portable.vibe_coding import VibeCodingCheckpoint, VibeStage
from portable.vibe_coding_host import (
    DurableVibeCheckpointStore,
    IsolatedGitWorktree,
    LocalProjectTool,
    LocalTestExecutionTool,
    LocalVibeCodingHost,
    MCPExecutionAuthorization,
    MCPStdioToolAdapter,
    MCPToolCallBinding,
    MCPTrustedExecutionContext,
    JournaledToolAdapter,
    build_mcp_authority_dispatch,
    mcp_action_id,
    mcp_action_idempotency_key,
    mcp_request_action_id,
    mcp_request_digest,
    SideEffectJournal,
    VerifiedResearchTool,
    fingerprint_worktree,
    load_host_config,
    _build_provider_operation_lookups,
    _read_bounded_mcp_stdout_frame,
)


def init_git(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "auren-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "AUREN Test"], check=True)
    (root / "README.md").write_text("# Sample project\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()


def fake_mcp_server(path: Path, *, plain_text: bool = False, structured: dict | None = None) -> None:
    result = (
        {"content": [{"type": "text", "text": "Results: https://example.com/product and https://docs.example.org/alternatives"}]}
        if plain_text else
        {"structuredContent": structured or {"market_evidence": [{"source": "example.com", "locator": "https://example.com/product"}], "competitors": ["example.com"], "evidence": [{"source": "example.com", "locator": "https://example.com/product"}]}}
    )
    source = """import json, sys
result = json.loads(%r)
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"protocolVersion":"2024-11-05","capabilities":{},"serverInfo":{"name":"fake-mcp","version":"1"}}}), flush=True)
    elif method == "tools/call":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":result}), flush=True)
""" % json.dumps(result)
    path.write_text(source, encoding="utf-8")


def fake_operation_lookup_server(path: Path) -> None:
    source = """import json, sys
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"protocolVersion":"2024-11-05","capabilities":{},"serverInfo":{"name":"fake-operation-lookup","version":"1"}}}), flush=True)
    elif method == "tools/call":
        args = msg.get("params", {}).get("arguments", {})
        result = {"structuredContent": {
            "status": "not_found",
            "operation_id": args.get("operation_id"),
            "operation_key": args.get("idempotency_key"),
        }}
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":result}), flush=True)
"""
    path.write_text(source, encoding="utf-8")


class VibeCodingHostTests(unittest.TestCase):
    def test_isolated_worktree_never_mutates_source_checkout_and_rollback_requires_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            revision = init_git(source)
            lease = IsolatedGitWorktree(source, "host-test", base / "worktrees")
            worktree = lease.open()
            self.assertNotEqual(worktree, source)
            self.assertEqual(lease.base_revision, revision)
            (worktree / "src").mkdir()
            (worktree / "src" / "feature.py").write_text("value = 1\n", encoding="utf-8")
            self.assertFalse((source / "src" / "feature.py").exists())
            with self.assertRaisesRegex(PermissionError, "explicit operator authorization"):
                lease.rollback()
            self.assertEqual(lease.rollback(authorized=True), revision)
            self.assertFalse((worktree / "src").exists())
            status = subprocess.run(["git", "-C", str(source), "status", "--porcelain"], check=True, text=True, capture_output=True)
            self.assertEqual(status.stdout.strip(), "")

    def test_local_repository_and_test_adapters_use_real_git_and_subprocess_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            base_revision = init_git(root)
            (root / "src").mkdir()
            (root / "src" / "feature.py").write_text("value = 42\n", encoding="utf-8")
            project = LocalProjectTool(root, base_revision, allow_workspace_writes=True)
            result = project({
                "stage": "development", "capability": "repository",
                "state": {"base_revision": base_revision},
                "prior_stage_outputs": {},
            })
            self.assertIn("src/feature.py", result["artifacts"])
            tests = LocalTestExecutionTool(root, [(sys.executable, "-c", "print('host-test-ok')")])
            test_result = tests({"stage": "testing", "state": {}})
            self.assertEqual(test_result["status"], "passed")
            self.assertEqual(test_result["tests_run"][0]["returncode"], 0)
            self.assertTrue(test_result["evidence"][0]["locator"].startswith("command://"))


    def test_oversized_mcp_stdout_frame_is_rejected_without_draining_child_stream(self):
        class OversizedWithoutNewline:
            def __init__(self):
                self.read_sizes = []

            def readline(self, size):
                self.read_sizes.append(size)
                if len(self.read_sizes) == 1:
                    return "x" * size
                raise AssertionError("oversized frame reader must not wait for a newline")

        stream = OversizedWithoutNewline()
        frame, oversized = _read_bounded_mcp_stdout_frame(stream, 10)
        self.assertIsNone(frame)
        self.assertTrue(oversized)
        self.assertEqual(stream.read_sizes, [11])

    def test_oversized_mcp_stdout_frame_is_rejected_after_bounded_chunk_reads(self):
        class OversizedWithoutNewline:
            def __init__(self):
                self.read_sizes = []

            def readline(self, size):
                self.read_sizes.append(size)
                return "x" * size

        stream = OversizedWithoutNewline()
        frame, oversized = _read_bounded_mcp_stdout_frame(stream, 9000)
        self.assertIsNone(frame)
        self.assertTrue(oversized)
        self.assertEqual(stream.read_sizes, [4096, 4096, 809])
        self.assertLessEqual(max(stream.read_sizes), 4096)

    def test_mcp_stdout_frame_is_reassembled_from_bounded_chunks(self):
        class ChunkedLine:
            def __init__(self, data):
                self.data = data
                self.read_sizes = []

            def readline(self, size):
                self.read_sizes.append(size)
                if not self.data:
                    return ""
                newline = self.data.find("\\n")
                take = min(size, newline + 1 if newline >= 0 else len(self.data))
                part, self.data = self.data[:take], self.data[take:]
                return part

        expected = "x" * 9000
        stream = ChunkedLine(expected + "\\n")
        frame, oversized = _read_bounded_mcp_stdout_frame(stream, 10000)
        self.assertEqual(frame, expected)
        self.assertFalse(oversized)
        self.assertGreater(len(stream.read_sizes), 1)
        self.assertLessEqual(max(stream.read_sizes), 4096)

    def test_mcp_stdio_adapter_fails_closed_on_response_queue_flood(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            init_git(project)
            server = base / "flood_mcp.py"
            server.write_text(
                "import json, sys\n"
                "for line in sys.stdin:\n"
                "    try: msg = json.loads(line)\n"
                "    except Exception: continue\n"
                "    if msg.get('method') == 'initialize':\n"
                "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'flood','version':'1'}}}), flush=True)\n"
                "        for n in range(1000): print(json.dumps({'jsonrpc':'2.0','method':'notifications/progress','params':{'n':n}}), flush=True)\n"
                "    elif msg.get('method') == 'tools/call':\n"
                "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'structuredContent':{'ok':True}}}), flush=True)\n",
                encoding="utf-8",
            )
            adapter = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="bounded",
                timeout_seconds=3,
                requires_authority_binding=False,
                trusted_workspace_root=project,
            )
            with self.assertRaisesRegex(AdapterUnavailable, "bounded response queue"):
                adapter({"state": {"workspace_root": str(project)}, "intent": "test"})

    def test_mcp_stdio_adapter_calls_real_json_rpc_tool_and_research_requires_source_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            init_git(project)
            server = base / "fake_mcp.py"
            fake_mcp_server(server, plain_text=True)
            search = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="web_search",
                argument_map={"query": "$intent"},
                timeout_seconds=5,
                requires_authority_binding=False,
                trusted_workspace_root=project,
            )
            raw = search({
                "stage": "research", "intent": "market alternatives",
                "state": {"workspace_root": str(project)},
            })
            self.assertIn("https://example.com/product", raw["text"])
            research = VerifiedResearchTool(search, min_sources=2)
            result = research({
                "stage": "research", "intent": "market alternatives",
                "state": {"workspace_root": str(project), "vibe_coding_workflow_id": "mcp-test"},
                "prior_stage_outputs": {"idea": {"problem": "compare tools"}},
            })
            self.assertGreaterEqual(len(result["market_evidence"]), 2)
            self.assertEqual(len(result["competitors"]), 2)
            self.assertTrue(all(item["locator"].startswith("https://") for item in result["evidence"]))


    def test_unbound_mcp_dispatch_rejects_missing_or_escaping_trusted_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "trusted"
            outside = Path(temp) / "outside"
            root.mkdir()
            outside.mkdir()
            with self.assertRaisesRegex(ValueError, "trusted workspace root"):
                MCPStdioToolAdapter(
                    argv=(sys.executable, "-c", "pass"),
                    tool_name="research",
                    timeout_seconds=5,
                    requires_authority_binding=False,
                )
            tool = MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="research",
                timeout_seconds=5,
                requires_authority_binding=False,
                trusted_workspace_root=root,
            )
            request = {"stage": "research", "intent": "test", "state": {"workspace_root": str(outside)}}
            with patch("portable.vibe_coding_host.subprocess.Popen") as popen:
                with self.assertRaisesRegex(AdapterUnavailable, "escapes the trusted workspace root"):
                    tool(request)
                popen.assert_not_called()

    def test_unbound_mcp_dispatch_rejects_missing_workspace_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tool = MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="research",
                timeout_seconds=5,
                requires_authority_binding=False,
                trusted_workspace_root=root,
            )
            with patch("portable.vibe_coding_host.subprocess.Popen") as popen:
                with self.assertRaisesRegex(AdapterUnavailable, "explicit workspace root"):
                    tool({"stage": "research", "intent": "test", "state": {}})
                popen.assert_not_called()

    def test_mcp_structured_content_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            init_git(project)
            server = base / "fake_mcp.py"
            fake_mcp_server(server)
            tool = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="research",
                argument_map={"query": "$intent"},
                timeout_seconds=5,
                requires_authority_binding=False,
                trusted_workspace_root=project,
            )
            result = tool({"stage": "research", "intent": "test", "state": {"workspace_root": str(project)}})
            self.assertEqual(result["competitors"], ["example.com"])

    @staticmethod
    def _mcp_trusted_context(
        request, *, repository_revision="abc123", repository_digest="repo-digest",
        budget_id="budget-mcp-1",
    ):
        state = request["state"]
        project_root = str(Path(state.get("project_root") or Path.cwd()).expanduser().resolve())
        workspace_root = str(Path(state.get("workspace_root") or project_root).expanduser().resolve())
        return MCPTrustedExecutionContext(
            workflow_id=state["vibe_coding_workflow_id"],
            task_id=state["task_id"],
            project_root=project_root,
            workspace_root=workspace_root,
            repository_revision=repository_revision,
            repository_digest=repository_digest,
            budget_id=budget_id,
        )

    def _mcp_authorization(
        self, request, ledger, trusted_context, call_binding, *, authority_principal=None,
    ):
        operation = request["provider_operation"]
        run_id = trusted_context.workflow_id
        task_id = trusted_context.task_id
        action_request = ActionRequest(
            run_id=run_id,
            task_id=task_id,
            action_id=mcp_action_id(operation["operation_id"], request, call_binding.digest),
            actor="agent:publisher",
            capability="repository.write",
            action="publish",
            target="src/module.py",
            risk="external_effect",
            idempotency_key=mcp_action_idempotency_key(
                operation["operation_id"], request, call_binding.digest,
            ),
            evidence_refs=(
                f"provider-operation:{operation['operation_id']}",
                f"provider-idempotency:{operation['idempotency_key']}",
                f"mcp-request:{mcp_request_digest(request)}",
                f"mcp-call:{call_binding.digest}",
            ),
        )
        authority = ExecutionAuthorityBinding(
            execution_id="exec-mcp-1",
            run_id=run_id,
            task_id=task_id,
            repository_digest=trusted_context.repository_digest,
            repository_revision=trusted_context.repository_revision,
            principal=authority_principal or "agent:publisher",
            allowed_capabilities=("repository.write",),
            allowed_actions=("publish",),
            allowed_targets=("src",),
            budget_id=trusted_context.budget_id,
            max_attempts=3,
        )
        gateway = ActionGovernanceGateway(
            ActionPolicy(
                allowed_capabilities=frozenset({"repository.write"}),
                allowed_actions=frozenset({"publish"}),
                approval_required_actions=frozenset(),
                evidence_required_actions=frozenset(),
                external_effect_actions=frozenset({"publish"}),
                require_idempotency_for_external_effects=True,
                require_durable_ledger_for_external_effects=True,
            ),
            side_effect_ledger=ledger,
        )
        return MCPExecutionAuthorization(
            gateway=gateway,
            authority=authority,
            action_request=action_request,
            repository_revision=trusted_context.repository_revision,
            repository_digest=trusted_context.repository_digest,
            budget_id=trusted_context.budget_id,
            trusted_context=trusted_context,
            execution_guard=RuntimeExecutionGuard(max_steps=10, max_attempts=3),
            provider_operation_ledger=ledger,
        ), gateway

    @staticmethod
    def _mcp_external_request():
        root = str(Path.cwd().resolve())
        return {
            "stage": "production_deploy",
            "state": {
                "vibe_coding_workflow_id": "workflow-mcp-1",
                "task_id": "task-mcp-1",
                "project_root": root,
                "workspace_root": root,
            },
            "provider_operation": {
                "operation_id": "auren-op-mcp-1",
                "idempotency_key": "vibe-stage:workflow-mcp-1:production_deploy:release:v1",
                "provider": "release",
                "adapter_version": "v1",
            },
        }

    @unittest.skipUnless(os.name == "nt", "Windows Job Object process-tree enforcement")
    def test_mcp_stdio_kills_descendant_processes_on_return(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "descendant-survived.txt"
            server = root / "mcp_server.py"
            server.write_text(
                "import json, subprocess, sys\n"
                "marker = sys.argv[1]\n"
                "for line in sys.stdin:\n"
                " msg = json.loads(line)\n"
                " if msg.get('method') == 'initialize':\n"
                "  print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'tree-test','version':'1'}}}), flush=True)\n"
                " elif msg.get('method') == 'tools/call':\n"
                "  code = 'import pathlib,time,sys; time.sleep(1.5); pathlib.Path(sys.argv[1]).write_text(chr(115)+chr(117)+chr(114)+chr(118)+chr(105)+chr(118)+chr(101)+chr(100))'\n"
                "  subprocess.Popen([sys.executable, '-c', code, marker], close_fds=True)\n"
                "  print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'structuredContent':{'ok':True}}}), flush=True)\n",
                encoding="utf-8",
            )
            tool = MCPStdioToolAdapter(
                argv=(sys.executable, str(server), str(marker)),
                tool_name="tree_test",
                timeout_seconds=5,
                requires_authority_binding=False,
                trusted_workspace_root=root,
            )
            result = tool({"stage": "research", "intent": "terminate descendants", "state": {"workspace_root": str(root)}})
            self.assertTrue(result["ok"])
            time.sleep(2)
            self.assertFalse(marker.exists(), "MCP descendants must not survive adapter cleanup")

    def test_mcp_response_limit_rejects_invalid_values(self):
        with self.assertRaisesRegex(ValueError, "response limit"):
            MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="research",
                max_response_chars=0,
            )

    def test_mcp_oversized_response_is_rejected(self):
        import queue

        responses = queue.Queue()
        responses.put(MCPStdioToolAdapter._OVERSIZE_RESPONSE)
        with self.assertRaisesRegex(AdapterUnavailable, "character limit"):
            MCPStdioToolAdapter._read_response(None, responses, 2, 0.1)

    def test_mcp_adapter_rejects_oversized_tool_result_before_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            server = Path(temp) / "oversized_mcp.py"
            server.write_text(
                "import json, sys\n"
                "for line in sys.stdin:\n"
                "    request = json.loads(line)\n"
                "    if request.get('id') == 1:\n"
                "        result = {'protocolVersion': '2024-11-05', 'capabilities': {}, 'serverInfo': {'name': 'fake', 'version': '1'}}\n"
                "        print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result}), flush=True)\n"
                "    elif request.get('id') == 2:\n"
                "        result = {'content': [{'type': 'text', 'text': 'x' * 2000}]}\n"
                "        print(json.dumps({'jsonrpc': '2.0', 'id': 2, 'result': result}), flush=True)\n",
                encoding="utf-8",
            )
            tool = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="research",
                timeout_seconds=5,
                max_response_chars=512,
                requires_authority_binding=False,
                trusted_workspace_root=project,
            )
            with self.assertRaisesRegex(AdapterUnavailable, "character limit"):
                tool({"stage": "research", "intent": "oversized", "state": {"workspace_root": str(project)}})

    def test_mcp_adapter_defaults_to_authority_binding_and_fails_before_launch(self):
        tool = MCPStdioToolAdapter(
            argv=(sys.executable, "-c", "pass"),
            tool_name="publish",
            timeout_seconds=5,
        )
        with patch("portable.vibe_coding_host.subprocess.Popen") as popen:
            with self.assertRaisesRegex(AdapterUnavailable, "trusted execution authority resolver"):
                tool(self._mcp_external_request())
            popen.assert_not_called()

    def test_consequential_mcp_adapter_uses_bound_authority_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server = root / "fake_mcp.py"
            fake_mcp_server(server, structured={"ok": True})
            ledger = DurableSideEffectLedger(root / "side-effects.sqlite")
            gateways = []

            def resolver(request, call_binding):
                context = self._mcp_trusted_context(request)
                authorization, gateway = self._mcp_authorization(request, ledger, context, call_binding)
                gateways.append(gateway)
                return authorization

            request = self._mcp_external_request()
            provider_operation = request["provider_operation"]
            provider_key = provider_operation["idempotency_key"]
            provider_intent = {key: value for key, value in request.items() if key != "provider_operation"}
            claimed = ledger.begin(provider_key, {
                "provider": provider_operation["provider"],
                "operation_id": provider_operation["operation_id"],
                "intent": provider_intent,
            })
            self.assertEqual(claimed.action, "execute")
            tool = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="publish",
                timeout_seconds=5,
                requires_authority_binding=True,
                authority_dispatch=build_mcp_authority_dispatch(resolver),
            )
            result = tool(request)
            ledger.complete(provider_key, {
                "status": "completed",
                "provider_operation_id": provider_operation["operation_id"],
                "result": result,
            })
            self.assertEqual(result, {"ok": True})
            self.assertEqual(len(gateways), 1)
            self.assertTrue(gateways[0].verify_trace()["passed"])
            self.assertEqual(ledger.get(provider_key)["state"], "completed")
            call_binding = MCPToolCallBinding.from_call(
                "publish", (sys.executable, str(server)), {"request": dict(request)}, request,
                env_allowlist=(), timeout_seconds=5,
            )
            self.assertEqual(
                ledger.get(mcp_action_idempotency_key(
                    provider_operation["operation_id"], request, call_binding.digest,
                ))["state"],
                "completed",
            )
            second_tool_request = {
                **request,
                "capability": "preview",
                "tool_results": {"deployment": {"status": "passed"}},
            }
            self.assertNotEqual(
                mcp_action_id(provider_operation["operation_id"], request),
                mcp_action_id(provider_operation["operation_id"], second_tool_request),
            )
            self.assertNotEqual(
                mcp_action_idempotency_key(provider_operation["operation_id"], request),
                mcp_action_idempotency_key(provider_operation["operation_id"], second_tool_request),
            )

    def test_consequential_mcp_authority_denial_never_launches_process(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = DurableSideEffectLedger(Path(temp) / "side-effects.sqlite")
            request = self._mcp_external_request()
            operation = request["provider_operation"]
            ledger.begin(operation["idempotency_key"], {
                "provider": operation["provider"],
                "operation_id": operation["operation_id"],
                "intent": {key: value for key, value in request.items() if key != "provider_operation"},
            })

            def resolver(request, call_binding):
                context = self._mcp_trusted_context(request)
                authorization, _gateway = self._mcp_authorization(
                    request, ledger, context, call_binding, authority_principal="agent:someone-else",
                )
                return authorization

            tool = MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="publish",
                timeout_seconds=5,
                requires_authority_binding=True,
                authority_dispatch=build_mcp_authority_dispatch(resolver),
            )
            with patch("portable.vibe_coding_host.subprocess.Popen") as popen:
                with self.assertRaisesRegex(AdapterUnavailable, "principal_mismatch"):
                    tool(request)
                popen.assert_not_called()

    def test_consequential_mcp_adapter_requires_matching_durable_provider_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = DurableSideEffectLedger(Path(temp) / "side-effects.sqlite")
            request = self._mcp_external_request()

            def resolver(request, call_binding):
                context = self._mcp_trusted_context(request)
                authorization, _gateway = self._mcp_authorization(request, ledger, context, call_binding)
                return authorization

            tool = MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="publish",
                timeout_seconds=5,
                requires_authority_binding=True,
                authority_dispatch=build_mcp_authority_dispatch(resolver),
            )
            with patch("portable.vibe_coding_host.subprocess.Popen") as popen:
                with self.assertRaisesRegex(AdapterUnavailable, "authority resolution failed closed"):
                    tool(request)
                popen.assert_not_called()

    def test_mcp_read_only_authority_binds_exact_request_without_provider_operation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server = root / "fake_mcp.py"
            fake_mcp_server(server, structured={"read": True})
            gateways = []

            def resolver(request, call_binding):
                context = self._mcp_trusted_context(
                    request,
                    repository_revision="read-revision",
                    repository_digest="repo-read-digest",
                    budget_id="budget-mcp-read",
                )
                run_id = context.workflow_id
                task_id = context.task_id
                action_request = ActionRequest(
                    run_id=run_id,
                    task_id=task_id,
                    action_id=mcp_request_action_id(request, call_binding.digest),
                    actor="agent:reader",
                    capability="repository.read",
                    action="read",
                    target="src/module.py",
                    risk="read",
                    evidence_refs=(
                        f"mcp-request:{mcp_request_digest(request)}",
                        f"mcp-call:{call_binding.digest}",
                    ),
                )
                authority = ExecutionAuthorityBinding(
                    execution_id="exec-mcp-read",
                    run_id=run_id,
                    task_id=task_id,
                    repository_digest=context.repository_digest,
                    repository_revision=context.repository_revision,
                    principal="agent:reader",
                    allowed_capabilities=("repository.read",),
                    allowed_actions=("read",),
                    allowed_targets=("src",),
                    budget_id=context.budget_id,
                    max_attempts=2,
                )
                gateway = ActionGovernanceGateway(ActionPolicy(
                    allowed_capabilities=frozenset({"repository.read"}),
                    allowed_actions=frozenset({"read"}),
                    approval_required_actions=frozenset(),
                    evidence_required_actions=frozenset(),
                    external_effect_actions=frozenset(),
                ))
                gateways.append(gateway)
                return MCPExecutionAuthorization(
                    gateway=gateway,
                    authority=authority,
                    action_request=action_request,
                    repository_revision=context.repository_revision,
                    repository_digest=context.repository_digest,
                    budget_id=context.budget_id,
                    trusted_context=context,
                    execution_guard=RuntimeExecutionGuard(max_steps=5, max_attempts=2),
                )

            root_path = str(Path.cwd().resolve())
            request = {
                "stage": "architecture",
                "intent": "inspect repository structure",
                "state": {
                    "vibe_coding_workflow_id": "workflow-read-1",
                    "task_id": "task-read-1",
                    "project_root": root_path,
                    "workspace_root": root_path,
                },
            }
            tool = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="inspect",
                timeout_seconds=5,
                requires_authority_binding=True,
                authority_dispatch=build_mcp_authority_dispatch(resolver),
            )
            self.assertEqual(tool(request), {"read": True})
            self.assertTrue(gateways[0].verify_trace()["passed"])

    def test_mcp_authority_binds_configured_tool_and_command_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = DurableSideEffectLedger(Path(temp) / "side-effects.sqlite")
            request = self._mcp_external_request()
            operation = request["provider_operation"]
            ledger.begin(operation["idempotency_key"], {
                "provider": operation["provider"],
                "operation_id": operation["operation_id"],
                "intent": {key: value for key, value in request.items() if key != "provider_operation"},
            })
            context = self._mcp_trusted_context(request)
            expected_binding = MCPToolCallBinding.from_call(
                "publish",
                (sys.executable, "-c", "pass"),
                {"request": dict(request)},
                request,
                env_allowlist=(),
                timeout_seconds=5,
            )

            def resolver(actual_request, _actual_binding):
                authorization, _gateway = self._mcp_authorization(
                    actual_request, ledger, context, expected_binding,
                )
                return authorization

            tool = MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="delete",
                timeout_seconds=5,
                requires_authority_binding=True,
                authority_dispatch=build_mcp_authority_dispatch(resolver),
            )
            with patch("portable.vibe_coding_host.subprocess.Popen") as popen:
                with self.assertRaisesRegex(AdapterUnavailable, "authority resolution failed closed"):
                    tool(request)
                popen.assert_not_called()

    def test_mcp_read_only_path_rejects_policy_classified_external_effect(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = DurableSideEffectLedger(Path(temp) / "side-effects.sqlite")
            root_path = str(Path.cwd().resolve())
            request = {
                "stage": "research",
                "intent": "read-only request",
                "state": {
                    "vibe_coding_workflow_id": "workflow-read-1",
                    "task_id": "task-read-1",
                    "project_root": root_path,
                    "workspace_root": root_path,
                },
            }
            context = MCPTrustedExecutionContext(
                workflow_id="workflow-read-1",
                task_id="task-read-1",
                project_root=root_path,
                workspace_root=root_path,
                repository_revision="read-revision",
                repository_digest="repo-read-digest",
                budget_id="budget-mcp-read",
            )

            def resolver(actual_request, call_binding):
                action_request = ActionRequest(
                    run_id=context.workflow_id,
                    task_id=context.task_id,
                    action_id=mcp_request_action_id(actual_request, call_binding.digest),
                    actor="agent:reader",
                    capability="repository.read",
                    action="publish",
                    target="src/module.py",
                    risk="read",
                    idempotency_key="mcp-read-external-action",
                    evidence_refs=(
                        f"mcp-request:{mcp_request_digest(actual_request)}",
                        f"mcp-call:{call_binding.digest}",
                    ),
                )
                authority = ExecutionAuthorityBinding(
                    execution_id="exec-mcp-read-external",
                    run_id=context.workflow_id,
                    task_id=context.task_id,
                    repository_digest=context.repository_digest,
                    repository_revision=context.repository_revision,
                    principal="agent:reader",
                    allowed_capabilities=("repository.read",),
                    allowed_actions=("publish",),
                    allowed_targets=("src",),
                    budget_id=context.budget_id,
                    max_attempts=2,
                )
                gateway = ActionGovernanceGateway(ActionPolicy(
                    allowed_capabilities=frozenset({"repository.read"}),
                    allowed_actions=frozenset({"publish"}),
                    approval_required_actions=frozenset(),
                    evidence_required_actions=frozenset(),
                    external_effect_actions=frozenset({"publish"}),
                    require_idempotency_for_external_effects=True,
                    require_durable_ledger_for_external_effects=True,
                ), side_effect_ledger=ledger)
                return MCPExecutionAuthorization(
                    gateway=gateway,
                    authority=authority,
                    action_request=action_request,
                    repository_revision=context.repository_revision,
                    repository_digest=context.repository_digest,
                    budget_id=context.budget_id,
                    trusted_context=context,
                    execution_guard=RuntimeExecutionGuard(max_steps=5, max_attempts=2),
                )

            tool = MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="publish",
                timeout_seconds=5,
                requires_authority_binding=True,
                authority_dispatch=build_mcp_authority_dispatch(resolver),
            )
            with patch("portable.vibe_coding_host.subprocess.Popen") as popen:
                with self.assertRaisesRegex(AdapterUnavailable, "authority resolution failed closed"):
                    tool(request)
                popen.assert_not_called()

    def test_mcp_request_cannot_override_host_derived_task_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = DurableSideEffectLedger(Path(temp) / "side-effects.sqlite")
            trusted_request = self._mcp_external_request()
            request = self._mcp_external_request()
            request["state"]["task_id"] = "attacker-selected-task"
            operation = request["provider_operation"]
            ledger.begin(operation["idempotency_key"], {
                "provider": operation["provider"],
                "operation_id": operation["operation_id"],
                "intent": {key: value for key, value in request.items() if key != "provider_operation"},
            })
            trusted_context = self._mcp_trusted_context(trusted_request)

            def resolver(actual_request, call_binding):
                authorization, _gateway = self._mcp_authorization(
                    actual_request, ledger, trusted_context, call_binding,
                )
                return authorization

            tool = MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="publish",
                timeout_seconds=5,
                requires_authority_binding=True,
                authority_dispatch=build_mcp_authority_dispatch(resolver),
            )
            with patch("portable.vibe_coding_host.subprocess.Popen") as popen:
                with self.assertRaisesRegex(AdapterUnavailable, "authority resolution failed closed"):
                    tool(request)
                popen.assert_not_called()

    def test_mcp_call_binding_covers_command_environment_and_timeout(self):
        request = self._mcp_external_request()
        arguments = {"request": dict(request)}
        argv = (sys.executable, "-c", "pass")
        baseline = MCPToolCallBinding.from_call(
            "publish", argv, arguments, request, env_allowlist=("DEPLOY_TOKEN",), timeout_seconds=5,
        )
        changed_command = MCPToolCallBinding.from_call(
            "publish", (sys.executable, "-c", "other"), arguments, request,
            env_allowlist=("DEPLOY_TOKEN",), timeout_seconds=5,
        )
        changed_environment = MCPToolCallBinding.from_call(
            "publish", argv, arguments, request, env_allowlist=("OTHER_TOKEN",), timeout_seconds=5,
        )
        changed_timeout = MCPToolCallBinding.from_call(
            "publish", argv, arguments, request, env_allowlist=("DEPLOY_TOKEN",), timeout_seconds=6,
        )
        self.assertNotEqual(baseline.digest, changed_command.digest)
        self.assertNotEqual(baseline.digest, changed_environment.digest)
        self.assertNotEqual(baseline.digest, changed_timeout.digest)

    def test_mcp_config_defaults_consequential_tools_to_authority_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "host.json"
            config = {
                "schema_version": "auren-vibe-host-config-v1",
                "provider": "codex",
                "allow_workspace_writes": True,
                "test_commands": [[sys.executable, "-c", "pass"]],
                "mcp_tools": {
                    "security": {"argv": [sys.executable, "-c", "pass"], "tool_name": "scan", "external_effects": False},
                    "repository": {"argv": [sys.executable, "-c", "pass"], "tool_name": "edit"},
                },
                "browser_qa_required": False,
                "browser_qa_rationale": "Browser QA is not in this test scope",
                "preview_deployment_required": False,
                "preview_deployment_rationale": "No preview deployment is configured",
            }
            path.write_text(json.dumps(config), encoding="utf-8")
            os.chmod(path, 0o600)
            loaded = load_host_config(path)
            self.assertFalse(loaded.mcp_tools["security"]["external_effects"])
            self.assertTrue(loaded.mcp_tools["repository"]["external_effects"])

            config["mcp_tools"]["repository"]["external_effects"] = False
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot disable mandatory execution authority binding"):
                load_host_config(path)

    def test_provider_operation_event_projection_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = SideEffectJournal(Path(temp) / "operations.sqlite3")
            event = {
                "event_type": "provider_operation",
                "operation_key": "workflow:preview_deployment:deployment",
                "operation_id": "auren-op-test",
                "intent_digest": "a" * 64,
                "state": "completed",
                "provider": "deployment-adapter",
            }
            journal.record_provider_event(event)
            journal.record_provider_event({**event, "replay": True})
            with closing(sqlite3.connect(Path(temp) / "operations.sqlite3")) as db, db:
                rows = db.execute(
                    "SELECT operation_key,state,intent_digest FROM provider_operation_events"
                ).fetchall()
            self.assertEqual(
                [("workflow:preview_deployment:deployment", "completed", "a" * 64)],
                rows,
            )


    def test_provider_operation_reconcile_required_state_is_projected(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = SideEffectJournal(Path(temp) / "operations.sqlite3")
            event = {
                "event_type": "provider_operation",
                "operation_key": "workflow:development:deployment",
                "operation_id": "auren-op-reconcile",
                "intent_digest": "b" * 64,
                "state": "reconcile_required",
                "reason": "provider operation ID missing",
            }
            journal.record_provider_event(event)
            with closing(sqlite3.connect(Path(temp) / "operations.sqlite3")) as db, db:
                row = db.execute(
                    "SELECT state,details_json FROM provider_operation_events WHERE operation_key=?",
                    ("workflow:development:deployment",),
                ).fetchone()
            self.assertEqual("reconcile_required", row[0])
            self.assertIn("provider operation ID missing", row[1])

    def test_side_effect_journal_reuses_completed_receipt_without_replaying(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            init_git(root)
            journal = SideEffectJournal(Path(temp) / "state" / "operations.sqlite3")
            calls = []
            def delegate(_request):
                calls.append("called")
                return {"deployment_receipt": "receipt-1", "status": "passed"}
            adapter = JournaledToolAdapter(delegate, journal, capability="agent")
            request = {
                "stage": "development",
                "state": {"vibe_coding_workflow_id": "journal-test", "workspace_root": str(root)},
                "intent": "change a feature",
            }
            self.assertEqual(adapter(request)["deployment_receipt"], "receipt-1")
            self.assertEqual(adapter(request)["deployment_receipt"], "receipt-1")
            self.assertEqual(calls, ["called"])
            self.assertEqual(journal.status("journal-test", "development", "agent")["status"], "completed")

    def test_uncertain_side_effect_fails_closed_until_operator_reviews_exact_worktree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            init_git(root)
            journal = SideEffectJournal(Path(temp) / "state" / "operations.sqlite3")
            calls = []
            def flaky(_request):
                calls.append("called")
                (root / "partial.txt").write_text("partial operation", encoding="utf-8")
                if len(calls) == 1:
                    raise RuntimeError("simulated process interruption after partial write")
                return {"artifacts": ["partial.txt"], "status": "passed"}
            adapter = JournaledToolAdapter(flaky, journal, capability="agent")
            request = {
                "stage": "development",
                "state": {"vibe_coding_workflow_id": "uncertain-test", "workspace_root": str(root)},
                "intent": "change a feature",
            }
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                adapter(request)
            with self.assertRaisesRegex(Exception, "uncertain outcome"):
                adapter(request)
            self.assertEqual(calls, ["called"])
            reviewed_digest = fingerprint_worktree(root)
            with self.assertRaisesRegex(PermissionError, "explicit operator authorization"):
                journal.resolve_uncertain(
                    "uncertain-test", "development", "agent", action="retry",
                    workspace_root=root, expected_worktree_digest=reviewed_digest,
                    external_state_evidence="operator://review-1", reason="Reviewed partial write",
                )
            journal.resolve_uncertain(
                "uncertain-test", "development", "agent", action="retry",
                workspace_root=root, expected_worktree_digest=reviewed_digest,
                external_state_evidence="operator://review-1", reason="Reviewed partial write",
                authorized=True,
            )
            self.assertEqual(adapter(request)["status"], "passed")
            self.assertEqual(calls, ["called", "called"])

    def test_durable_checkpoint_is_bound_to_project_intent_head_and_adapter_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DurableVibeCheckpointStore(root / "state")
            snapshot = VibeCodingCheckpoint("workflow-1", (), {}).snapshot()
            path = store.save(
                snapshot, project_key="project-key", intent_digest="intent-digest",
                base_revision="a" * 40, workspace_root=str(root / "worktree"),
                adapter_config_digest="config-digest",
            )
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            loaded = store.load(
                "workflow-1", project_key="project-key", intent_digest="intent-digest",
                base_revision="a" * 40, workspace_root=str(root / "worktree"),
                adapter_config_digest="config-digest",
            )
            self.assertEqual(loaded, snapshot)
            with self.assertRaisesRegex(ValueError, "binding mismatch"):
                store.load(
                    "workflow-1", project_key="other-project", intent_digest="intent-digest",
                    base_revision="a" * 40, workspace_root=str(root / "worktree"),
                    adapter_config_digest="config-digest",
                )
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["base_revision"] = "b" * 40
            path.write_text(json.dumps(raw), encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                store.load(
                    "workflow-1", project_key="project-key", intent_digest="intent-digest",
                    base_revision="a" * 40, workspace_root=str(root / "worktree"),
                    adapter_config_digest="config-digest",
                )

    @unittest.skipIf(os.name == "nt", "isolated host integration currently targets POSIX provider CLIs")
    def test_complete_host_workflow_on_isolated_clone_of_real_auren_project(self):
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "auren-project"
            subprocess.run(
                ["git", "clone", "--local", "--no-hardlinks", str(repository), str(project)],
                check=True, text=True, capture_output=True,
            )
            bin_dir = base / "bin"
            bin_dir.mkdir()
            codex = bin_dir / "codex"
            codex_source = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
if "--version" in sys.argv:
    print("codex host-test 1.0")
    raise SystemExit(0)
prompt = json.loads(sys.stdin.read())
stage = prompt["stage"]
outputs = {
 "idea": {"problem": "Make a small, testable Python feature", "success_metrics": ["acceptance tests pass"]},
 "define_user": {"personas": ["developer"], "user_needs": ["simple deterministic behavior"]},
 "prd": {"requirements": ["implement addition"], "acceptance_criteria": ["add returns the sum"]},
 "choose_tech_stack": {"technology_decisions": ["Python standard library"], "tradeoffs": ["minimal dependencies"]},
 "architecture": {"architecture_decisions": ["keep add in src/feature.py"]},
 "task_breakdown": {"tasks": ["implement add", "run tests"]},
 "development": {"artifacts": ["src/feature.py"]},
}
if stage == "development":
    Path("src").mkdir(parents=True, exist_ok=True)
    Path("src/feature.py").write_text("def add(a, b): return a + b" + chr(10), encoding="utf-8")
output = dict(outputs.get(stage, {}))
output["status"] = "passed"
if stage == "development":
    output["provider_operation_id"] = "codex-host-test-development-42"
output["evidence"] = [{"source": "codex-host-test", "locator": "agent://" + stage}]
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(output)}}))
"""
            codex.write_text(codex_source, encoding="utf-8")
            codex.chmod(0o755)
            search_server = base / "search_mcp.py"
            security_server = base / "security_mcp.py"
            review_server = base / "review_mcp.py"
            fake_mcp_server(search_server, plain_text=True)
            fake_mcp_server(security_server, structured={
                "findings": [], "critical_findings": 0, "status": "passed",
                "evidence": [{"source": "test-security-tool", "locator": "security://diff-scan"}],
            })
            fake_mcp_server(review_server, structured={
                "reviewer": "independent-mcp-reviewer", "review_summary": "Reviewed isolated diff",
                "status": "passed", "blocking_findings": 0,
                "evidence": [{"source": "test-review-tool", "locator": "review://isolated-diff"}],
            })
            config_path = base / "host.json"
            config = {
                "schema_version": "auren-vibe-host-config-v1",
                "provider": "codex",
                "allow_workspace_writes": True,
                "test_commands": [[
                    sys.executable, "-m", "unittest",
                    "tests.test_vibe_coding", "tests.test_vibe_coding_adapters",
                ]],
                "env_allowlist": ["PYTHONPATH"],
                "mcp_tools": {
                    "web_search": {
                        "argv": [sys.executable, str(search_server)], "tool_name": "search",
                        "argument_map": {"query": "$intent"},
                    },
                    "security": {
                        "argv": [sys.executable, str(security_server)], "tool_name": "scan",
                        "external_effects": False,
                        "argument_map": {"request": "$request"},
                    },
                    "independent_review": {
                        "argv": [sys.executable, str(review_server)], "tool_name": "review",
                        "argument_map": {"request": "$request"},
                    },
                },
                "browser_qa_required": False,
                "browser_qa_rationale": "This integration target validates a Python library, not a browser UI",
                "preview_deployment_required": False,
                "preview_deployment_rationale": "No external preview deployment is authorized in CI",
                "security_tool_required": True,
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")
            os.chmod(config_path, 0o600)
            previous_path = os.environ.get("PATH", "")
            previous_pythonpath = os.environ.get("PYTHONPATH")
            os.environ["PATH"] = str(bin_dir) + os.pathsep + previous_path
            os.environ["PYTHONPATH"] = str(project) + os.pathsep + str(project / ".ai-harness")
            try:
                host_config = load_host_config(config_path)
                try:
                    report = LocalVibeCodingHost(project, config=host_config, state_root=base / "auren-state").run(
                        "auren-host-e2e", "Implement a small deterministic addition feature"
                    )
                except RuntimeError as exc:
                    marker = "report="
                    report_path = str(exc).split(marker, 1)[1] if marker in str(exc) else ""
                    detail = Path(report_path).read_text(encoding="utf-8") if report_path and Path(report_path).is_file() else "no report file"
                    self.fail(f"{exc}\\nReport: {detail}")
            finally:
                os.environ["PATH"] = previous_path
                if previous_pythonpath is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = previous_pythonpath
            self.assertEqual(report["run_status"], "accepted", report["stop_reason"])
            self.assertEqual(len(report["stage_statuses"]), 18)
            self.assertFalse(report["production_deployment_performed"])
            self.assertFalse(report["merge_or_publish_performed"])
            self.assertIn("src/feature.py", report["workspace_changed_files"])
            self.assertTrue((Path(report["workspace_root"]) / "src" / "feature.py").is_file())
            self.assertFalse((project / "src" / "feature.py").exists())
            self.assertTrue(Path(report["learning_receipt"]).is_file())
            self.assertTrue(Path(report["checkpoint_receipt"]).is_file())
            # The stage-level durable receipt ledger and host side-effect journal
            # must share one operations database, not drift into parallel stores.
            with closing(sqlite3.connect(base / "auren-state" / "operations.sqlite3")) as db, db:
                tables = {
                    row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            self.assertIn("operations", tables)
            self.assertIn("durable_side_effects", tables)
            self.assertIn("provider_operation_events", tables)
            with closing(sqlite3.connect(base / "auren-state" / "operations.sqlite3")) as db, db:
                event_count = db.execute("SELECT COUNT(*) FROM provider_operation_events").fetchone()[0]
            self.assertGreater(event_count, 0)
            source_status = subprocess.run(
                ["git", "-C", str(project), "status", "--porcelain"],
                check=True, text=True, capture_output=True,
            ).stdout
            self.assertEqual(source_status.strip(), "")

    @unittest.skipIf(os.name == "nt", "POSIX file permission contract")

    def test_provider_operation_lookup_mcp_port_receives_exact_operation_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server = root / "lookup_mcp.py"
            fake_operation_lookup_server(server)
            operations = _build_provider_operation_lookups(
                {
                    "deployment": {
                        "operation_lookup": {
                            "argv": [sys.executable, str(server)],
                            "tool_name": "lookup_operation",
                            "argument_map": {
                                "operation_id": "$operation_id",
                                "idempotency_key": "$idempotency_key",
                            },
                            "timeout_seconds": 5,
                        },
                    },
                },
                workspace_root=root,
            )
            self.assertIn(VibeStage.PREVIEW_DEPLOYMENT, operations)
            self.assertNotIn(VibeStage.PRODUCTION_DEPLOY, operations)
            result = operations[VibeStage.PREVIEW_DEPLOYMENT](
                "auren-op-bound-42", "workflow:preview:deployment",
            )
            self.assertEqual("not_found", result["status"])
            self.assertEqual("auren-op-bound-42", result["operation_id"])
            self.assertEqual("workflow:preview:deployment", result["operation_key"])


    def test_preview_deployment_config_requires_valid_provider_operation_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "host.json"
            config = {
                "schema_version": "auren-vibe-host-config-v1",
                "provider": "codex",
                "allow_workspace_writes": True,
                "test_commands": [[sys.executable, "-c", "pass"]],
                "mcp_tools": {
                    "security": {"argv": [sys.executable, "-c", "pass"], "tool_name": "scan", "external_effects": False},
                    "deployment": {"argv": [sys.executable, "-c", "pass"], "tool_name": "deploy"},
                    "preview": {"argv": [sys.executable, "-c", "pass"], "tool_name": "preview"},
                },
                "browser_qa_required": False,
                "browser_qa_rationale": "No browser surface in this test",
                "preview_deployment_required": True,
                "preview_deployment_rationale": "Preview deployment is explicitly configured",
            }
            path.write_text(json.dumps(config), encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(ValueError, "provider-native operation_lookup"):
                load_host_config(path)

            config["mcp_tools"]["deployment"]["operation_lookup"] = {
                "argv": [sys.executable, "-c", "pass"],
                "tool_name": "lookup",
                "argument_map": {
                    "operation_id": "$operation_id",
                    "idempotency_key": "$idempotency_key",
                },
                "timeout_seconds": 5,
            }
            path.write_text(json.dumps(config), encoding="utf-8")
            loaded = load_host_config(path)
            lookup = loaded.mcp_tools["deployment"]["operation_lookup"]
            self.assertEqual("lookup", lookup["tool_name"])
            self.assertEqual(5, lookup["timeout_seconds"])
            self.assertEqual("$operation_id", lookup["argument_map"]["operation_id"])

    @unittest.skipIf(os.name == "nt", "POSIX file permission contract")
    def test_host_config_requires_private_file_and_explicit_security_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "host.json"
            config = {
                "schema_version": "auren-vibe-host-config-v1",
                "provider": "codex",
                "reviewer_provider": "claude",
                "allow_workspace_writes": True,
                "test_commands": [[sys.executable, "-m", "unittest", "discover", "-s", "tests"]],
                "mcp_tools": {
                    "security": {"argv": [sys.executable, "-c", "pass"], "tool_name": "scan", "external_effects": False},
                },
                "browser_qa_required": False,
                "browser_qa_rationale": "Project has no browser surface in this validation scope",
                "preview_deployment_required": False,
                "preview_deployment_rationale": "No preview environment is configured for this validation",
            }
            path.write_text(json.dumps(config), encoding="utf-8")
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(PermissionError, "private"):
                load_host_config(path)
            os.chmod(path, 0o600)
            loaded = load_host_config(path)
            self.assertTrue(loaded.allow_workspace_writes)
            self.assertTrue(loaded.security_tool_required)
            self.assertFalse(loaded.browser_qa_required)


if __name__ == "__main__":
    unittest.main()
