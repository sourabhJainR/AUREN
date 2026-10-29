from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.agent_execution_guard import AgentAssignmentScopeDenied, agent_assignment_scope
from portable.vibe_coding_host import MCPStdioToolAdapter


class MCPAssignedAgentPathSelectorTests(unittest.TestCase):
    def test_conflicting_target_path_is_denied_before_mcp_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            adapter = MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="read-only",
                timeout_seconds=3,
                max_response_chars=220,
                requires_authority_binding=False,
                trusted_workspace_root=workspace,
            )
            request = {
                "path": "allowed.txt",
                "target_path": "blocked.txt",
                "state": {"workspace_root": str(workspace)},
            }
            with agent_assignment_scope(("allowed.txt",)):
                with patch.object(
                    adapter,
                    "_invoke_unchecked",
                    side_effect=AssertionError("MCP process must not launch for an out-of-scope selector"),
                ) as dispatch:
                    with self.assertRaisesRegex(AgentAssignmentScopeDenied, "outside the assigned scope"):
                        adapter(request)
                    dispatch.assert_not_called()

    def test_conflicting_nested_path_selector_is_denied_before_mcp_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            adapter = MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="read-only",
                timeout_seconds=3,
                max_response_chars=220,
                requires_authority_binding=False,
                trusted_workspace_root=workspace,
            )
            request = {
                "path": "allowed.txt",
                "state": {
                    "workspace_root": str(workspace),
                    "repository_path": "blocked.txt",
                },
            }
            with agent_assignment_scope(("allowed.txt",)):
                with patch.object(
                    adapter,
                    "_invoke_unchecked",
                    side_effect=AssertionError("MCP process must not launch for an out-of-scope selector"),
                ) as dispatch:
                    with self.assertRaisesRegex(AgentAssignmentScopeDenied, "outside the assigned scope"):
                        adapter(request)
                    dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
