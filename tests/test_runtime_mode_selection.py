from __future__ import annotations

import json
from pathlib import Path

import pytest

from portable.onboarding import OnboardingError, initialize_repository, load_project_profile, set_runtime_mode


def test_init_defaults_to_single_user_local_without_external_factory(tmp_path: Path):
    initialize_repository(tmp_path)
    path, profile = load_project_profile(tmp_path)
    assert path == tmp_path / ".auren" / "project.json"
    assert profile["runtime_mode"] == "single_user_local"
    assert profile["managed_runtime"] == {"enabled": False, "runtime_factory": None}


def test_managed_mode_requires_explicit_opt_in_and_preserves_backup(tmp_path: Path):
    initialize_repository(tmp_path)
    preview = set_runtime_mode(tmp_path, "managed", runtime_factory="host.runtime:create_runtime")
    assert preview["applied"] is False
    assert preview["profile"]["managed_runtime"] == {
        "enabled": True, "runtime_factory": "host.runtime:create_runtime",
    }
    assert load_project_profile(tmp_path)[1]["runtime_mode"] == "single_user_local"

    applied = set_runtime_mode(
        tmp_path, "managed", apply=True, runtime_factory="host.runtime:create_runtime",
    )
    assert applied["applied"] is True
    assert Path(applied["backup"]).is_file()
    path, profile = load_project_profile(tmp_path)
    assert profile["runtime_mode"] == "managed"
    assert profile["managed_runtime"]["enabled"] is True
    assert profile["managed_runtime"]["runtime_factory"] == "host.runtime:create_runtime"


def test_switching_back_to_local_disables_and_clears_managed_factory(tmp_path: Path):
    initialize_repository(tmp_path)
    set_runtime_mode(tmp_path, "managed", apply=True, runtime_factory="host.runtime:create_runtime")
    result = set_runtime_mode(tmp_path, "single_user_local", apply=True)
    assert result["runtime_mode"] == "single_user_local"
    profile = load_project_profile(tmp_path)[1]
    assert profile["managed_runtime"] == {"enabled": False, "runtime_factory": None}


def test_local_mode_rejects_runtime_factory_and_managed_mode_needs_opt_in(tmp_path: Path):
    initialize_repository(tmp_path)
    with pytest.raises(OnboardingError, match="not part of single-user local setup"):
        set_runtime_mode(tmp_path, "single_user_local", runtime_factory="host.runtime:create_runtime")
    path = tmp_path / ".auren" / "project.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["runtime_mode"] = "managed"
    profile["managed_runtime"] = {"enabled": False, "runtime_factory": None}
    path.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(OnboardingError, match="requires explicit"):
        load_project_profile(tmp_path)


def test_local_profile_with_stale_managed_factory_is_rejected(tmp_path: Path):
    initialize_repository(tmp_path)
    path = tmp_path / ".auren" / "project.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["managed_runtime"] = {
        "enabled": False, "runtime_factory": "stale.factory:create_runtime",
    }
    path.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(OnboardingError, match="cannot enable or retain"):
        load_project_profile(tmp_path)


def test_invalid_mode_and_invalid_factory_are_rejected(tmp_path: Path):
    initialize_repository(tmp_path)
    with pytest.raises(OnboardingError, match="runtime mode"):
        set_runtime_mode(tmp_path, "team")
    with pytest.raises(OnboardingError, match="module.path:function"):
        set_runtime_mode(tmp_path, "managed", runtime_factory="not-a-reference")
