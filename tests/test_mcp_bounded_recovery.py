"""Regression tests for bounded MCP frame recovery and read-only chunking."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from portable.vibe_coding_adapters import AdapterUnavailable
from portable.vibe_coding_host import MCPStdioToolAdapter, _read_bounded_mcp_stdout_frame


def _write_paged_mcp_server(path: Path) -> None:
    # "all" deliberately returns a frame over the configured cap. The adapter
    # must retry only through the operator-supplied read-only chunk plan.
    source = r"""
import json, sys
for line in sys.stdin:
    try:
        message = json.loads(line)
    except Exception:
        continue
    method = message.get("method")
    if method == "initialize":
        response = {"jsonrpc":"2.0","id":message["id"],"result":{"protocolVersion":"2024-11-05","capabilities":{},"serverInfo":{"name":"bounded-recovery-test","version":"1"}}}
        print(json.dumps(response), flush=True)
    elif method == "tools/call":
        query = message.get("params", {}).get("arguments", {}).get("query")
        if query == "all":
            result = {"structuredContent":{"items":["x" * 1800]}}
        elif query == "page-a":
            result = {"structuredContent":{"items":["a" * 400]}}
        elif query == "page-b":
            result = {"structuredContent":{"items":["b" * 400]}}
        else:
            result = {"structuredContent":{"items":[]}}
        print(json.dumps({"jsonrpc":"2.0","id":message["id"],"result":result}), flush=True)
"""
    path.write_text(source, encoding="utf-8")


class BoundedMCPRecoveryContractTests(unittest.TestCase):
    def test_oversized_frame_detection_reads_only_bounded_prefix(self):
        class Stream:
            def __init__(self):
                self.calls = 0

            def readline(self, size):
                self.calls += 1
                self.asserted_size = size
                return "z" * size

        stream = Stream()
        frame, oversized, metadata = _read_bounded_mcp_stdout_frame(stream, 100)
        self.assertIsNone(frame)
        self.assertTrue(oversized)
        self.assertEqual(stream.calls, 1)
        self.assertEqual(stream.asserted_size, 101)
        self.assertEqual(metadata["observed_chars_at_least"], 101)
        self.assertEqual(len(metadata["observed_prefix_sha256"]), 64)
        self.assertLessEqual(len(metadata["sample_prefix"]), 96)

    def test_read_only_oversized_response_recovers_by_explicit_chunks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server = root / "mcp_server.py"
            _write_paged_mcp_server(server)
            tool = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="search",
                argument_map={"query": "$intent"},
                timeout_seconds=5,
                max_response_chars=1200,
                max_aggregate_response_chars=3000,
                requires_authority_binding=False,
                trusted_workspace_root=root,
                read_only_response_chunker=lambda _args: [
                    {"query": "page-a"}, {"query": "page-b"},
                ],
                read_only_response_merger=lambda pages: {
                    "items": [item for page in pages for item in page["items"]],
                },
            )
            result = tool({
                "stage": "research",
                "intent": "all",
                "state": {"workspace_root": str(root)},
            })
            self.assertEqual(len(result["items"]), 2)
            self.assertEqual([item[0] for item in result["items"]], ["a", "b"])

    def test_read_only_chunk_recovery_rejects_aggregate_budget_overflow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server = root / "mcp_server.py"
            _write_paged_mcp_server(server)
            tool = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="search",
                argument_map={"query": "$intent"},
                timeout_seconds=5,
                max_response_chars=1200,
                max_aggregate_response_chars=1200,
                requires_authority_binding=False,
                trusted_workspace_root=root,
                read_only_response_chunker=lambda _args: [
                    {"query": "page-a"}, {"query": "page-b"},
                ],
                read_only_response_merger=lambda pages: {
                    "items": [item for page in pages for item in page["items"]],
                },
            )
            with self.assertRaisesRegex(AdapterUnavailable, "aggregate response budget"):
                tool({
                    "stage": "research",
                    "intent": "all",
                    "state": {"workspace_root": str(root)},
                })

    def test_adaptive_chunking_is_rejected_for_authority_bound_tools(self):
        with self.assertRaisesRegex(ValueError, "operator-declared read-only"):
            MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="publish",
                read_only_response_chunker=lambda args: [args, {**args, "page": 2}],
                read_only_response_merger=lambda pages: {"pages": list(pages)},
            )


if __name__ == "__main__":
    unittest.main()
