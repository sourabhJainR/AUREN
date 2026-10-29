from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from portable.vibe_coding_host import MCPStdioToolAdapter
from portable.vibe_coding_adapters import AdapterUnavailable


class MCPOversizedFrameProcessTerminationTests(unittest.TestCase):
    def test_oversized_unterminated_response_fails_fast_and_terminates_child(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            server = Path(temp) / "oversized_mcp.py"
            server.write_text(
                "import json, sys, time\n"
                "for line in sys.stdin:\n"
                "    try: msg = json.loads(line)\n"
                "    except Exception: continue\n"
                "    if msg.get('method') == 'initialize':\n"
                "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'oversized','version':'1'}}}), flush=True)\n"
                "    elif msg.get('method') == 'tools/call':\n"
                "        sys.stdout.write('x' * 1000000)\n"
                "        sys.stdout.flush()\n"
                "        while True: time.sleep(1)\n",
                encoding="utf-8",
            )
            adapter = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="oversized",
                argument_map={"query": "$query"},
                timeout_seconds=3,
                max_response_chars=128,
                requires_authority_binding=False,
                trusted_workspace_root=root,
            )
            real_popen = subprocess.Popen
            children = []

            def capture_child(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                children.append(child)
                return child

            with patch("portable.vibe_coding_host.subprocess.Popen", side_effect=capture_child):
                with self.assertRaisesRegex(AdapterUnavailable, "exceeded the configured character limit"):
                    adapter({"query": "bounded", "state": {"workspace_root": str(root)}})

            self.assertEqual(1, len(children), "the MCP adapter should launch exactly one child")
            self.assertIsNotNone(
                children[0].poll(),
                "an oversized unterminated MCP response must terminate the child process",
            )
            children[0].wait(timeout=1)


if __name__ == "__main__":
    unittest.main()
