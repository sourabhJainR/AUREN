from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from portable.vibe_coding import VibeCodingCheckpoint
from portable.vibe_coding_host import (
    DurableVibeCheckpointStore,
    IsolatedGitWorktree,
    LocalProjectTool,
    LocalTestExecutionTool,
    MCPStdioToolAdapter,
    VerifiedResearchTool,
    load_host_config,
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


def fake_mcp_server(path: Path, *, plain_text: bool = False) -> None:
    result = (
        {"content": [{"type": "text", "text": "Results: https://example.com/product and https://docs.example.org/alternatives"}]}
        if plain_text else
        {"structuredContent": {"market_evidence": [{"source": "example.com", "locator": "https://example.com/product"}], "competitors": ["example.com"], "evidence": [{"source": "example.com", "locator": "https://example.com/product"}]}}
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
            )
            result = tool({"stage": "research", "intent": "test", "state": {"workspace_root": str(project)}})
            self.assertEqual(result["competitors"], ["example.com"])

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
                    "security": {"argv": [sys.executable, "-c", "pass"], "tool_name": "scan"},
                },
                "browser_qa_required": False,
                "browser_qa_rationale": "Project has no browser surface in this validation scope",
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
