from __future__ import annotations

import json
import unittest

from portable.agency_observability import Trace, bind_trace_context
from portable.crew_adapters import ApprovedTool, ApprovedToolRegistry


class ApprovedToolObservabilityTests(unittest.TestCase):
    def test_tool_invocation_creates_parented_span_without_raw_arguments_or_results(self):
        registry = ApprovedToolRegistry([
            ApprovedTool(
                "read_context",
                lambda _arguments: {"content": "private repository text"},
                "Read-only context lookup",
            )
        ])
        trace = Trace("supervised crew")
        parent = trace.span("crew.execute", phase="execution")

        with bind_trace_context(trace, parent.span_id):
            result = registry.invoke("read_context", {"query": "private query"})

        self.assertEqual(result, {"content": "private repository text"})
        self.assertEqual(len(trace.spans), 2)
        tool_span = trace.spans[1]
        self.assertEqual(tool_span.name, "tool.read_context")
        self.assertEqual(tool_span.kind, "tool")
        self.assertEqual(tool_span.phase, "tool")
        self.assertEqual(tool_span.parent_id, parent.span_id)
        self.assertEqual(tool_span.status, "ok")
        self.assertIsNotNone(tool_span.end)
        serialized = json.dumps(trace.as_dict(), sort_keys=True)
        self.assertNotIn("private query", serialized)
        self.assertNotIn("private repository text", serialized)
        self.assertEqual(tool_span.input, {"argument_keys": ["query"]})
        self.assertTrue(tool_span.output["result_digest"])

    def test_tool_exception_closes_span_as_error_and_preserves_exception(self):
        def fail(_arguments):
            raise RuntimeError("private provider detail")

        registry = ApprovedToolRegistry([
            ApprovedTool("read_context", fail, "Read-only context lookup")
        ])
        trace = Trace("supervised crew")
        parent = trace.span("crew.execute", phase="execution")

        with bind_trace_context(trace, parent.span_id):
            with self.assertRaisesRegex(RuntimeError, "private provider detail"):
                registry.invoke("read_context", {"query": "private query"})

        tool_span = trace.spans[1]
        self.assertEqual(tool_span.status, "error")
        self.assertIsNotNone(tool_span.end)
        serialized = json.dumps(trace.as_dict(), sort_keys=True)
        self.assertNotIn("private provider detail", serialized)
        self.assertNotIn("private query", serialized)

    def test_tool_invocation_without_trace_context_keeps_existing_behavior(self):
        registry = ApprovedToolRegistry([
            ApprovedTool("read_context", lambda arguments: dict(arguments), "Read-only context lookup")
        ])
        self.assertEqual(registry.invoke("read_context", {"query": "hello"}), {"query": "hello"})


if __name__ == "__main__":
    unittest.main()
