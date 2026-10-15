"""Context compaction must never truncate protected contract/evidence sections."""
import pytest

from pathlib import Path
import sys

# Runtime module lives under the harness directory rather than the Python package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness" / "runtime"))
from auto_compaction import compact


def test_compaction_fails_closed_if_protected_sections_cannot_fit():
    source = "## GOAL\n" + ("g" * 620) + "\n## ACCEPTANCE\n" + ("a" * 620)
    with pytest.raises(ValueError, match="protected context sections exceed"):
        compact(source, budget_chars=1000)


def test_compaction_preserves_protected_sections_verbatim():
    goal = "Complete the task without weakening safety constraints."
    acceptance = "Keep the evidence lineage and rollback proof."
    source = f"## GOAL\n{goal}\n## ACCEPTANCE\n{acceptance}\n## NOTES\n" + ("repeated note " * 300)
    result = compact(source, budget_chars=1200)
    assert f"## GOAL\n{goal}" in result.text
    assert f"## ACCEPTANCE\n{acceptance}" in result.text
    assert result.compacted
