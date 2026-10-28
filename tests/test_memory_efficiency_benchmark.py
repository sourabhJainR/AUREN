"""Tests for the reproducible AUREN memory-efficiency benchmark."""
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.memory_efficiency_benchmark import run_benchmark


class MemoryEfficiencyBenchmarkTests(unittest.TestCase):
    def test_benchmark_reports_exact_round_trip_and_complete_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.jsonl"
            records = [
                {"id": "security-1", "category": "security", "text": "Preserve Guard checks. " * 120, "verified": True, "confidence": 1.0},
                {"id": "lesson-1", "category": "lesson", "text": "Use bounded batches and stream results.", "verified": False, "confidence": 0.6},
            ]
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
            report = run_benchmark(path, chunk_chars=256)
            self.assertTrue(report["round_trip_exact"])
            self.assertEqual(report["record_coverage"], 1.0)
            self.assertEqual(report["critical_record_coverage"], 1.0)
            self.assertEqual(report["missing_record_ids"], [])
            self.assertEqual(report["missing_critical_record_ids"], [])
            self.assertLessEqual(report["max_observed_chunk_chars"], 256)
            self.assertTrue(report["promotion_eligible"])
            self.assertEqual(len(report["corpus_sha256"]), 64)

    def test_benchmark_rejects_duplicate_record_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.jsonl"
            row = {"id": "duplicate", "category": "lesson", "text": "same ID"}
            path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate record IDs"):
                run_benchmark(path, chunk_chars=256)


if __name__ == "__main__":
    unittest.main()
