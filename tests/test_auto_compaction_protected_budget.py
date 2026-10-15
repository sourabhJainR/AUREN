"""Context compaction must never truncate protected contract/evidence sections."""
from pathlib import Path
import sys
import unittest

# Runtime module lives under the harness directory rather than the Python package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness" / "runtime"))
from auto_compaction import compact


class AutoCompactionProtectedBudgetTests(unittest.TestCase):
    def test_compaction_fails_closed_if_protected_sections_cannot_fit(self):
        source = "## GOAL\n" + ("g" * 620) + "\n## ACCEPTANCE\n" + ("a" * 620)
        with self.assertRaisesRegex(ValueError, "protected context sections exceed"):
            compact(source, budget_chars=1000)

    def test_compaction_preserves_protected_sections_verbatim(self):
        goal = "Complete the task without weakening safety constraints."
        acceptance = "Keep the evidence lineage and rollback proof."
        source = f"## GOAL\n{goal}\n## ACCEPTANCE\n{acceptance}\n## NOTES\n" + ("repeated note " * 300)
        result = compact(source, budget_chars=1200)
        self.assertIn(f"## GOAL\n{goal}", result.text)
        self.assertIn(f"## ACCEPTANCE\n{acceptance}", result.text)
        self.assertTrue(result.compacted)


if __name__ == "__main__":
    unittest.main()
