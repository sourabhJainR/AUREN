from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portable.canonical_execution_plan import CanonicalExecutionPlan
from portable.vibe_coding_host import AdapterUnavailable, MCPStdioToolAdapter


class PlanBoundMCPToolSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.schema = {
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
            "additionalProperties": False,
        }
        self.arguments = {"query": "inspect"}
        self.plan = self.make_plan(self.schema, self.arguments)

    def tearDown(self):
        self.temp.cleanup()

    def make_plan(self, schema, arguments):
        return CanonicalExecutionPlan.from_mapping({
            "target": {
                "repository": "owner/product",
                "branch": "feature/schema-check",
                "base_sha": "a" * 40,
                "allowed_paths": ["src/**"],
                "governance_repository": "owner/auren",
            },
            "requirements": ["Use the configured MCP tool"],
            "acceptance": ["Live schema and arguments match the reviewed plan"],
            "required_capabilities": ["mcp"],
            "capabilities": [{"name": "mcp", "available": True, "evidence": "test fixture"}],
            "stopping_condition": "All schema checks pass",
            "tool_contracts": [{
                "name": "inspect",
                "schema": schema,
                "arguments": arguments,
            }],
        })

    def make_server(self, live_schema, marker):
        source = """
import json, sys
schema = json.loads(%r)
marker = %r
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        print(json.dumps({"jsonrpc":"2.0","id":message["id"],"result":{"protocolVersion":"2024-11-05","capabilities":{},"serverInfo":{"name":"schema-fixture","version":"1"}}}), flush=True)
    elif method == "tools/list":
        print(json.dumps({"jsonrpc":"2.0","id":message["id"],"result":{"tools":[{"name":"inspect","inputSchema":schema}]}}), flush=True)
    elif method == "tools/call":
        open(marker, "w", encoding="utf-8").write("called")
        print(json.dumps({"jsonrpc":"2.0","id":message["id"],"result":{"structuredContent":{"status":"ok"}}}), flush=True)
""" % (json.dumps(live_schema), str(marker))
        server = self.root / "mcp_fixture.py"
        server.write_text(source, encoding="utf-8")
        return server

    def adapter(self, schema, arguments=None):
        marker = self.root / "called.txt"
        server = self.make_server(schema, marker)
        adapter = MCPStdioToolAdapter(
            argv=(__import__("sys").executable, str(server)),
            tool_name="inspect",
            argument_map={"query": "$query"},
            timeout_seconds=5,
            requires_authority_binding=False,
            trusted_workspace_root=self.root,
            execution_plan=self.plan,
        )
        return adapter, marker, ({"query": "inspect"} if arguments is None else arguments)

    def test_live_schema_and_exact_arguments_are_required_before_call(self):
        adapter, marker, request_arguments = self.adapter(self.schema)
        result = adapter({"query": "inspect", "state": {"workspace_root": str(self.root)}})
        self.assertEqual({"status": "ok"}, result)
        self.assertTrue(marker.is_file())

    def test_schema_drift_blocks_before_tool_call(self):
        changed = dict(self.schema)
        changed["description"] = "unreviewed schema change"
        adapter, marker, _ = self.adapter(changed)
        with self.assertRaises(AdapterUnavailable):
            adapter({"query": "inspect", "state": {"workspace_root": str(self.root)}})
        self.assertFalse(marker.exists())

    def test_argument_change_blocks_before_tool_call(self):
        adapter, marker, _ = self.adapter(self.schema)
        with self.assertRaises(AdapterUnavailable):
            adapter({"query": "different", "state": {"workspace_root": str(self.root)}})
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
