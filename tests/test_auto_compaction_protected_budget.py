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

    def test_compaction_preserves_approval_failure_rollback_and_unresolved_decisions(self):
        sections = {
            "APPROVALS": "Human approval: operator-17 approved deployment.",
            "CONSTRAINTS": "Never send private context to remote providers.",
            "FAILURES": "Provider outage: request timed out.",
            "EVIDENCE LINEAGE": "Evidence ev-17 binds artifact sha256:abc.",
            "ROLLBACK": "Rollback to release-42 using migration down script.",
            "UNRESOLVED DECISIONS": "Decision pending: data retention owner.",
            "GUARD": "Guard denies production promotion without approval.",
        }
        source = "\\n".join(
            f"## {heading}\\n{body}" for heading, body in sections.items()
        ) + "\\n## NOTES\\n" + ("repeated note " * 400)
        result = compact(source, budget_chars=1800)
        for heading, body in sections.items():
            self.assertIn(f"## {heading}\\n{body}", result.text)

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
