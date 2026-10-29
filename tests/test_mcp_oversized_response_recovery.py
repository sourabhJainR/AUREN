from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from portable.vibe_coding_adapters import AdapterUnavailable
from portable.vibe_coding_host import MCPStdioToolAdapter


class MCPOversizedResponseRecoveryTests(unittest.TestCase):
    def test_read_only_call_reduces_batch_after_real_oversized_jsonrpc_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            server = root / "server.py"
            calls = root / "calls.txt"
            server.write_text(
                "import json, sys\n"
                "calls_path = " + repr(str(calls)) + "\n"
                "for line in sys.stdin:\n"
                "    try: msg = json.loads(line)\n"
                "    except Exception: continue\n"
                "    method = msg.get('method')\n"
                "    if method == 'initialize':\n"
                "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'oversize-test','version':'1'}}}), flush=True)\n"
                "    elif method == 'tools/call':\n"
                "        limit = msg.get('params', {}).get('arguments', {}).get('limit')\n"
                "        with open(calls_path, 'a', encoding='utf-8') as out: out.write(str(limit) + '\\n')\n"
                "        items = ['x' * 1800] if limit >= 3 else ['recovered']\n"
                "        response = {'jsonrpc':'2.0','id':msg['id'],'result':{'structuredContent':{'items':items,'limit':limit}}}\n"
                "        print(json.dumps(response), flush=True)\n",
                encoding="utf-8",
            )

            def reduce_batch(arguments):
                reduced = dict(arguments)
                reduced["limit"] = max(1, int(reduced["limit"]) - 1)
                return reduced

            adapter = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="read_records",
                argument_map={"limit": "$limit"},
                timeout_seconds=5,
                max_response_chars=512,
                requires_authority_binding=False,
                trusted_workspace_root=workspace,
                read_only_response_reducer=reduce_batch,
            )
            result = adapter({
                "stage": "research",
                "intent": "read a bounded page",
                "limit": 3,
                "state": {"workspace_root": str(workspace)},
            })

            self.assertEqual(result, {"items": ["recovered"], "limit": 2})
            self.assertEqual(calls.read_text(encoding="utf-8").splitlines(), ["3", "2"])

    def test_consequential_tools_cannot_use_oversized_response_recovery(self):
        with self.assertRaisesRegex(ValueError, "only allowed for operator-declared read-only"):
            MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="publish",
                read_only_response_reducer=lambda arguments: arguments,
            )


if __name__ == "__main__":
    unittest.main()
