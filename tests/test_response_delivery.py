"""Regression tests for lossless bounded response delivery."""
import unittest

from portable.response_delivery import (
    ResponseDeliveryError,
    assemble_response,
    chunk_response,
    chunks_after,
)


class ResponseDeliveryTests(unittest.TestCase):
    def test_round_trip_preserves_unicode_markdown_and_code_exactly(self):
        source = (
            "# Complete patch\n\n"
            "Do not drop security requirements. 東京🙂\n"
            "```python\n"
            "def example():\n"
            "    return 'exact bytes: 🔐'\n"
            "```\n"
        ) * 30
        manifest, chunks = chunk_response(source, max_bytes=180)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.utf8_bytes <= 180 for chunk in chunks))
        self.assertEqual(assemble_response(manifest, chunks), source)
        self.assertEqual(manifest.total_chars, len(source))
        self.assertEqual(manifest.total_bytes, len(source.encode("utf-8")))

    def test_long_single_line_and_unicode_tokens_are_bounded(self):
        source = ("界🙂" * 500) + ("x" * 1000)
        manifest, chunks = chunk_response(source, max_bytes=128)
        self.assertTrue(all(chunk.utf8_bytes <= 128 for chunk in chunks))
        self.assertEqual(assemble_response(manifest, chunks), source)

    def test_empty_response_has_explicit_completion_chunk(self):
        manifest, chunks = chunk_response("", max_bytes=32)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(assemble_response(manifest, chunks), "")

    def test_resume_returns_only_unacknowledged_chunks(self):
        _, chunks = chunk_response("paragraph\n\n" * 50, max_bytes=64)
        self.assertEqual(chunks_after(chunks, 0), chunks)
        self.assertEqual(chunks_after(chunks, 2), chunks[2:])
        self.assertEqual(chunks_after(chunks, len(chunks)), ())
        with self.assertRaises(ResponseDeliveryError):
            chunks_after(chunks, len(chunks) + 1)

    def test_missing_reordered_duplicate_or_corrupt_chunks_fail_closed(self):
        manifest, chunks = chunk_response("word " * 100, max_bytes=40)
        with self.assertRaises(ResponseDeliveryError):
            assemble_response(manifest, chunks[:-1])
        with self.assertRaises(ResponseDeliveryError):
            assemble_response(manifest, (chunks[1], chunks[0], *chunks[2:]))
        damaged = list(chunks)
        original = damaged[0]
        from dataclasses import replace
        damaged[0] = replace(original, text=original.text + "tampered")
        with self.assertRaises(ResponseDeliveryError):
            assemble_response(manifest, damaged)

    def test_invalid_budgets_rejected(self):
        for budget in (0, 1, 3, True, 1.5):
            with self.assertRaises(ValueError):
                chunk_response("content", max_bytes=budget)


if __name__ == "__main__":
    unittest.main()
