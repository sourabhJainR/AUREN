from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from portable import chat_capability


class ChatCapabilityMCPTests(unittest.TestCase):
    def test_stdio_server_negotiates_and_lists_the_trigger_tool(self):
        incoming = "\n".join([
            json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}),
            "",
        ])
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(incoming)), patch("sys.stdout", output):
            self.assertEqual(0, chat_capability.serve_mcp(runtime=object()))

        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([1, 2, 3], [item["id"] for item in responses])
        self.assertEqual("2025-03-26", responses[0]["result"]["protocolVersion"])
        tool = responses[1]["result"]["tools"][0]
        self.assertEqual("adaptive_runtime_trigger", tool["name"])
        self.assertIn("inputSchema", tool)
        self.assertEqual({}, responses[2]["result"])

    def test_stdio_server_calls_only_the_registered_trigger_tool(self):
        incoming = json.dumps({
            "jsonrpc": "2.0", "id": "call-1", "method": "tools/call",
            "params": {
                "name": "adaptive_runtime_trigger",
                "arguments": {"task": "test", "project_root": "/tmp/repo"},
            },
        }) + "\n"
        output = io.StringIO()
        receipt = {"trigger_id": "trigger-1", "status": "accepted", "accepted_at": "now"}
        with patch("sys.stdin", io.StringIO(incoming)), patch("sys.stdout", output), patch.object(
            chat_capability, "invoke", return_value=receipt
        ) as invoke:
            self.assertEqual(0, chat_capability.serve_mcp(runtime=object()))

        response = json.loads(output.getvalue())
        self.assertEqual("call-1", response["id"])
        self.assertEqual(receipt, json.loads(response["result"]["content"][0]["text"]))
        invoke.assert_called_once()

    def test_stdio_server_rejects_unknown_tools(self):
        incoming = json.dumps({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "arbitrary_write", "arguments": {}},
        }) + "\n"
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(incoming)), patch("sys.stdout", output):
            self.assertEqual(0, chat_capability.serve_mcp(runtime=object()))

        response = json.loads(output.getvalue())
        self.assertEqual(4, response["id"])
        self.assertEqual(-32602, response["error"]["code"])
        self.assertIn("unknown tool", response["error"]["message"])


if __name__ == "__main__":
    unittest.main()
