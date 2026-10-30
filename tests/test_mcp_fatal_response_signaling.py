from __future__ import annotations

import queue
import unittest

from portable.vibe_coding_host import _signal_mcp_fatal_response


class MCPFatalResponseSignalTests(unittest.TestCase):
    def test_fatal_marker_replaces_buffered_frames_without_blocking(self):
        responses: queue.Queue[str] = queue.Queue(maxsize=2)
        responses.put_nowait('{"jsonrpc":"2.0","method":"notifications/progress"}')
        responses.put_nowait('{"jsonrpc":"2.0","method":"notifications/log"}')

        marker = "\\x00AUREN_MCP_RESPONSE_TOO_LARGE\\x00:{\"observed_bytes_at_least\":8193}"
        _signal_mcp_fatal_response(responses, marker)

        self.assertEqual(responses.qsize(), 1)
        self.assertEqual(responses.get_nowait(), marker)
        self.assertTrue(responses.empty())

    def test_fatal_marker_is_delivered_to_an_empty_queue(self):
        responses: queue.Queue[str] = queue.Queue(maxsize=32)

        _signal_mcp_fatal_response(responses, "\\x00AUREN_MCP_RESPONSE_QUEUE_OVERFLOW\\x00")

        self.assertEqual(responses.get_nowait(), "\\x00AUREN_MCP_RESPONSE_QUEUE_OVERFLOW\\x00")

    def test_empty_or_non_string_marker_is_rejected(self):
        responses: queue.Queue[str] = queue.Queue(maxsize=1)
        for marker in ("", None, 42):
            with self.subTest(marker=marker):
                with self.assertRaises(ValueError):
                    _signal_mcp_fatal_response(responses, marker)  # type: ignore[arg-type]
        self.assertTrue(responses.empty())


if __name__ == "__main__":
    unittest.main()
