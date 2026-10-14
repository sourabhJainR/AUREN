from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

import engine


def deadline(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def test_deadline_remaining_seconds_requires_timezone_and_reports_expiry():
    assert engine.deadline_remaining_seconds(deadline(30)) > 0
    assert engine.deadline_remaining_seconds(deadline(-1)) < 0
    with pytest.raises(ValueError, match="timezone"):
        engine.deadline_remaining_seconds("2030-01-01T00:00:00")
    with pytest.raises(ValueError, match="ISO-8601"):
        engine.deadline_remaining_seconds("not-a-date")


def test_validation_does_not_start_commands_after_task_deadline():
    with tempfile.TemporaryDirectory() as tmp:
        config = {
            "validation": {
                "commands": [[sys.executable, "-c", "raise SystemExit(99)"]],
                "auto_discover": False,
            },
            "execution": {"validation_timeout_seconds": 60},
        }
        passed, results = engine.run_validation(
            config, Path(tmp), deadline_at=deadline(-1),
        )
        assert not passed
        assert results[0]["status"] == "deadline_exceeded"
        assert results[0]["exit_code"] == 124


def test_task_deadline_setting_is_bounded_in_config():
    import tomllib

    config = tomllib.loads((ROOT / ".ai-harness" / "config.toml").read_text(encoding="utf-8"))
    assert config["execution"]["task_deadline_seconds"] == 3600
