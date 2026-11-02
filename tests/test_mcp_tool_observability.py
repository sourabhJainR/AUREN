from __future__ import annotations

import json
import unittest
from pathlib import Path

from portable.agency_observability import Trace, bind_trace_context
from portable.vibe_coding_host import MCPStdioToolAdapter


class MCPToolObservabilityTests(unittest.TestCase):
    @staticmethod
    def adapter():
        return MCPStdioToolAdapter(
            argv=("python", "-c", "pass"),
            tool_name="read_context",
            requires_authority_binding=False,
            trusted_workspace_root=Path.cwd(),
        )

    def test_mcp_tool_success_emits_parented_span_without_payload_capture(self):
        adapter = self.adapter()
        adapter._invoke_unchecked = lambda _request, **_kwargs: {
            "content": "private repository output"
        }
        trace = Trace("supervised MCP")
        parent = trace.span("vibe.execute", phase="execution")

        with bind_trace_context(trace, parent.span_id):
            result = adapter({"request": {"query": "private user query"}})

        self.assertEqual(result, {"content": "private repository output"})
        self.assertEqual(len(trace.spans), 2)
        span = trace.spans[1]
        self.assertEqual(span.name, "tool.mcp.read_context")
        self.assertEqual(span.kind, "tool")
        self.assertEqual(span.phase, "tool")
        self.assertEqual(span.parent_id, parent.span_id)
        self.assertEqual(span.status, "ok")
        self.assertIsNotNone(span.end)
        self.assertEqual(span.input, {"request_keys": ["request"]})
        self.assertTrue(span.metadata["request_digest"])
        self.assertTrue(span.metadata["argv_digest"])
        self.assertTrue(span.output["result_digest"])
        serialized = json.dumps(trace.as_dict(), sort_keys=True)
        self.assertNotIn("private user query", serialized)
        self.assertNotIn("private repository output", serialized)

    def test_mcp_tool_exception_closes_span_without_exception_text(self):
        adapter = self.adapter()

        def fail(_request, **_kwargs):
            raise RuntimeError("private MCP stderr and token=secret-value")

        adapter._invoke_unchecked = fail
        trace = Trace("supervised MCP")
        parent = trace.span("vibe.execute", phase="execution")

        with bind_trace_context(trace, parent.span_id):
            with self.assertRaisesRegex(RuntimeError, "private MCP stderr"):
                adapter({"request": {"query": "private user query"}})

        span = trace.spans[1]
        self.assertEqual(span.status, "error")
        self.assertIsNotNone(span.end)
        serialized = json.dumps(trace.as_dict(), sort_keys=True)
        self.assertNotIn("private MCP stderr", serialized)
        self.assertNotIn("secret-value", serialized)
        self.assertNotIn("private user query", serialized)

    def test_mcp_tool_without_trace_context_preserves_result(self):
        adapter = self.adapter()
        adapter._invoke_unchecked = lambda _request, **_kwargs: {"ok": True}
        self.assertEqual(adapter({"request": {"query": "hello"}}), {"ok": True})


    def test_non_json_result_keeps_original_return_behavior(self):
        adapter = self.adapter()
        opaque = object()
        result = {"opaque": opaque}
        adapter._invoke_unchecked = lambda _request, **_kwargs: result
        trace = Trace("supervised MCP")
        parent = trace.span("vibe.execute", phase="execution")

        with bind_trace_context(trace, parent.span_id):
            observed = adapter({"request": {"query": "hello"}})

        self.assertIs(observed, result)
        self.assertIs(observed["opaque"], opaque)
        span = trace.spans[1]
        self.assertEqual(span.status, "ok")
        self.assertIsNotNone(span.end)
        self.assertIsNone(span.output["result_digest"])
        self.assertTrue(span.output["result_digest_unavailable"])


if __name__ == "__main__":
    unittest.main()
