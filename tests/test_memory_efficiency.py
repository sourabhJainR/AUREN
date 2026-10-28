"""Regression tests for lossless, bounded memory packing."""
import tempfile
import unittest
from pathlib import Path

from portable.agent_capabilities import MemoryRecord
from portable.memory_efficiency import (
    MemoryBudgetExceeded,
    chunk_memory_context,
    compress_snapshot,
    decompress_snapshot,
)
from portable.persistent_memory import PersistentMemory


class MemoryEfficiencyTests(unittest.TestCase):
    def test_compressed_snapshot_round_trips_exact_record_fields(self):
        rows = [
            MemoryRecord(
                id="m1", project="repo", category="security",
                text="Never bypass approval; preserve rollback evidence. 東京 🔐",
                intent_digest="intent-1", confidence=0.99, verified=True,
                created_at="2026-10-29T00:00:00+00:00",
                branch="main", source_revision="abc123",
            )
        ]
        packed = compress_snapshot(rows)
        self.assertLess(packed.compressed_bytes, packed.original_bytes + 256)
        restored = decompress_snapshot(packed.payload)
        self.assertEqual(restored[0], {
            "id": "m1", "project": "repo", "category": "security",
            "text": "Never bypass approval; preserve rollback evidence. 東京 🔐",
            "intent_digest": "intent-1", "confidence": 0.99, "verified": True,
            "created_at": "2026-10-29T00:00:00+00:00",
            "branch": "main", "source_revision": "abc123",
            "retracted_at": None, "retraction_reason": None,
        })
        self.assertEqual(packed.record_count, 1)

    def test_snapshot_corruption_fails_closed(self):
        packed = compress_snapshot([{"id": "x", "text": "keep exact evidence"}])
        damaged = packed.payload[:-1] + bytes([packed.payload[-1] ^ 0x01])
        with self.assertRaises(ValueError):
            decompress_snapshot(damaged)

    def test_chunking_preserves_every_record_and_respects_character_bound(self):
        rows = [
            {"id": "optional", "category": "lesson", "text": "small note", "verified": False, "confidence": 0.2},
            {"id": "critical", "category": "security", "text": "DO NOT weaken policy. " * 100, "verified": True, "confidence": 1.0},
            {"id": "unicode", "category": "requirement", "text": "東京🙂" * 80, "verified": True, "confidence": 0.9},
        ]
        chunks = chunk_memory_context(rows, max_chars=256)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk.text) <= 256 for chunk in chunks))
        emitted = [rid for chunk in chunks for rid in chunk.record_ids]
        self.assertEqual(set(emitted), {"optional", "critical", "unicode"})
        self.assertGreaterEqual(len(emitted), len(rows))
        self.assertTrue(all(chunk.source_digest for chunk in chunks))
        self.assertIn("category=security", chunks[0].text)

    def test_small_budget_rejects_instead_of_silently_dropping_content(self):
        with self.assertRaises(ValueError):
            chunk_memory_context([{"id": "x", "category": "security", "text": "keep"}], max_chars=100)

    def test_canonical_memory_facade_exposes_bounded_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = PersistentMemory(Path(tmp) / "memory.db", require_approval=False)
            memory.remember("p", "security", "Do not disable Guard checks.", approved=True, verified=True, confidence=1.0)
            memory.remember("p", "lesson", "Use bounded retries.", approved=True, confidence=0.5)
            chunks = memory.context_chunks("p", "Guard", max_chars=256, limit=10)
            self.assertTrue(chunks)
            self.assertTrue(all(len(chunk.text) <= 256 for chunk in chunks))
            self.assertTrue(any("Guard" in chunk.text for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
