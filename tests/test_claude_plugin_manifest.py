"""Regression coverage for Claude plugin skill manifest paths."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_claude_marketplace_skills_are_directories():
    manifest = json.loads(
        (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )

    skills = manifest["plugins"][0]["skills"]
    assert skills

    for skill in skills:
        path = Path(skill)
        assert path.name != "SKILL.md", (
            "Claude marketplace skill entries must point to the parent "
            "skill directory, not directly to SKILL.md"
        )
        skill_file = ROOT / path / "SKILL.md"
        assert skill_file.is_file(), f"Missing skill entry point: {skill_file}"


def test_claude_plugin_skill_root_is_directory_based():
    manifest = json.loads(
        (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    skill_root = ROOT / manifest["skills"].rstrip("/")
    assert skill_root.is_dir()
    assert (skill_root / "ai-coding-orchestrator" / "SKILL.md").is_file()
