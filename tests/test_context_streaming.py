from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.context_streaming import (
    DurableChunkJournal,
    choose_sample,
    file_sha256,
    iter_file_chunks,
    iter_text_chunks,
)


class ContextStreamingTests(unittest.TestCase):
    def test_chunks_reconstruct_source_without_overlap(self):
        source = ("alpha beta gamma\n" * 80) + "tail"
        chunks = list(iter_text_chunks(source, max_chars=128, overlap_chars=16))
        self.assertEqual("".join(chunk.text for chunk in chunks), source)
        self.assertTrue(all(chunk.sha256 and chunk.end - chunk.start == len(chunk.text) for chunk in chunks))

    def test_file_streaming_handles_utf8_and_bounded_reads(self):
        source = ("雪と🌱 line\n" * 100) + "done"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.txt"
            path.write_text(source, encoding="utf-8")
            chunks = list(iter_file_chunks(path, max_chars=96, overlap_chars=8, read_chars=37))
            rebuilt = chunks[0].text if chunks else ""
            for previous, current in zip(chunks, chunks[1:]):
                rebuilt += current.text[previous.end - current.start:]
            self.assertEqual(rebuilt, source)
            self.assertEqual(file_sha256(path), __import__("hashlib").sha256(source.encode()).hexdigest())

    def test_sampling_requires_explicit_exploratory_scope(self):
        chunks = list(iter_text_chunks("x" * 2000, max_chars=100, overlap_chars=10))
        with self.assertRaises(PermissionError):
            choose_sample(chunks, count=3)
        with self.assertRaises(PermissionError):
            choose_sample(chunks, count=3, exploratory=True, verification=True)
        sample = choose_sample(chunks, count=3, exploratory=True)
        self.assertEqual([chunks[0], chunks[len(chunks) // 2], chunks[-1]], sample)

    def test_journal_is_idempotent_and_rejects_source_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = DurableChunkJournal(Path(directory) / "progress.sqlite")
            chunk = list(iter_text_chunks("a" * 200, max_chars=100, overlap_chars=10))[0]
            journal.begin("job-1", "source-v1")
            journal.record("job-1", chunk, "verified result")
            journal.record("job-1", chunk, "verified result")
            self.assertEqual(journal.completed("job-1")[0], (chunk.sha256, "verified result"))
            with self.assertRaises(ValueError):
                journal.record("job-1", chunk, "conflicting result")
            with self.assertRaises(ValueError):
                journal.begin("job-1", "source-v2")
            journal.finish("job-1", blocked=True)
            with self.assertRaises(ValueError):
                journal.record("job-1", chunk, "late result")


if __name__ == "__main__":
    unittest.main()
