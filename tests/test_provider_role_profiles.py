from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from portable.crew_adapters import ApprovedToolRegistry, SOFTWARE_TEAM_PERSONA_IDS, SPECIALIST_CREW_IDS
from portable.provider_role_profiles import (
    ProviderRoleProfileError,
    build_profiled_crew_adapter_inputs,
    create_provider_role_profile_document,
    load_provider_role_profile,
    provider_role_profile_report,
    write_provider_role_profile,
)


def _write_profile(tmp_path: Path, *, provider: str = "ollama", model: str = "qwen2.5-coder:14b",
                   local_model: str | None = None) -> Path:
    path = tmp_path / ".auren" / "provider_roles.json"
    document = create_provider_role_profile_document(
        provider_id=provider,
        model_id=model,
        local_model_id=local_model,
    )
    write_provider_role_profile(path, document, apply=True)
    return path


def test_profile_init_creates_all_role_families_with_empty_tool_scopes(tmp_path: Path):
    path = _write_profile(tmp_path)
    profile = load_provider_role_profile(path)

    assert len(profile.roles) == len(SPECIALIST_CREW_IDS) + len(SOFTWARE_TEAM_PERSONA_IDS)
    assert set(profile.roles) == set(SPECIALIST_CREW_IDS) | set(SOFTWARE_TEAM_PERSONA_IDS)
    assert all(not binding.allowed_tools for binding in profile.roles.values())
    assert len(profile.digest) == 64
    assert path.stat().st_mode & 0o077 == 0


def test_profile_preview_does_not_write_and_existing_profile_is_never_overwritten(tmp_path: Path):
    path = tmp_path / ".auren" / "provider_roles.json"
    document = create_provider_role_profile_document(model_id="qwen2.5-coder:14b")
    preview = write_provider_role_profile(path, document, apply=False)
    assert preview["applied"] is False
    assert not path.exists()

    write_provider_role_profile(path, document, apply=True)
    before = path.read_bytes()
    with pytest.raises(ProviderRoleProfileError, match="refusing to overwrite"):
        write_provider_role_profile(path, document, apply=True)
    assert path.read_bytes() == before


def test_profile_requires_complete_role_families(tmp_path: Path):
    document = create_provider_role_profile_document(model_id="qwen2.5-coder:14b")
    document["roles"].pop("backend_api")
    path = tmp_path / "partial.json"
    with pytest.raises(ProviderRoleProfileError, match="complete family"):
        write_provider_role_profile(path, document, apply=True)


def test_profile_rejects_unknown_fields_and_embedded_credentials(tmp_path: Path):
    document = create_provider_role_profile_document(model_id="qwen2.5-coder:14b")
    document["roles"]["backend_api"]["api_key"] = "do-not-store"
    with pytest.raises(ProviderRoleProfileError, match="fields do not match"):
        write_provider_role_profile(tmp_path / "bad.json", document, apply=True)


def test_profile_rejects_mutable_model_aliases(tmp_path: Path):
    with pytest.raises(ProviderRoleProfileError, match="pinned model"):
        create_provider_role_profile_document(provider_id="openai", model_id="latest")


def test_profile_rejects_duplicate_json_keys(tmp_path: Path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"auren-provider-role-profile-v1","profile_id":"a","profile_id":"b"}', encoding="utf-8")
    with pytest.raises(ProviderRoleProfileError, match="duplicate"):
        load_provider_role_profile(path)


def test_profile_report_marks_remote_provider_disabled_without_privacy_opt_in(tmp_path: Path):
    path = _write_profile(tmp_path, provider="openai", model="gpt-5.1")
    report = provider_role_profile_report(
        load_provider_role_profile(path),
        environ={"OPENAI_API_KEY": "x" * 32},
    )
    assert report["route_policy_ready"] is False
    assert report["live_activation"] == "not_performed"
    assert report["private_data_routing"] == "local_only"
    assert report["roles"][0]["authenticated"] == "not_tested"


def test_profiled_adapter_inputs_keep_private_tasks_local_and_public_tasks_guarded(tmp_path: Path):
    path = _write_profile(
        tmp_path, provider="openai", model="gpt-5.1", local_model="qwen2.5-coder:14b",
    )

    class FakeProvider:
        def __init__(self):
            self.calls = 0

        def complete(self, prompt: str, *, timeout_seconds: float):
            self.calls += 1
            return SimpleNamespace(content="remote proposal")

    fake_provider = FakeProvider()
    local_calls = []
    env = {
        "OPENAI_API_KEY": "x" * 32,
        "AUREN_REMOTE_PROVIDER_ORDER": "openai",
        "AUREN_LOCAL_REMOTE_INFERENCE": "enabled",
        "AUREN_LOCAL_DATA_CLASSIFICATION": "public",
    }
    with (
        patch("portable.provider_role_profiles.OpenAICompatibleProvider.from_environment", return_value=fake_provider),
        patch("portable.provider_role_profiles._local_model_invoker",
              side_effect=lambda model: lambda prompt, timeout: local_calls.append((model, timeout)) or "local proposal"),
    ):
        prepared = build_profiled_crew_adapter_inputs(
            path, tools=ApprovedToolRegistry(), environ=env,
        )
        private_prompt = "specialist request\n" + json.dumps({
            "task_request": {"task": "inspect private source", "privacy": "private"},
        })
        public_prompt = "specialist request\n" + json.dumps({
            "task_request": {"task": "summarize public docs", "privacy": "public"},
        })
        private_result = prepared["specialists"]["backend_api"]["invoker"](private_prompt, 30)
        public_result = prepared["personas"]["cto_sponsor"]["invoker"](public_prompt, 30)

    assert len(prepared["specialists"]) == len(SPECIALIST_CREW_IDS)
    assert len(prepared["personas"]) == len(SOFTWARE_TEAM_PERSONA_IDS)
    assert private_result == "local proposal"
    assert public_result == "remote proposal"
    assert len(local_calls) == 1
    assert fake_provider.calls == 1


def test_remote_profile_without_local_fallback_denies_private_tasks(tmp_path: Path):
    path = _write_profile(tmp_path, provider="openai", model="gpt-5.1")

    class FakeProvider:
        def complete(self, *_args, **_kwargs):
            raise AssertionError("private task must not reach remote provider")

    env = {
        "OPENAI_API_KEY": "x" * 32,
        "AUREN_REMOTE_PROVIDER_ORDER": "openai",
        "AUREN_LOCAL_REMOTE_INFERENCE": "enabled",
        "AUREN_LOCAL_DATA_CLASSIFICATION": "public",
    }
    with patch("portable.provider_role_profiles.OpenAICompatibleProvider.from_environment", return_value=FakeProvider()):
        prepared = build_profiled_crew_adapter_inputs(path, tools=ApprovedToolRegistry(), environ=env)
    private_prompt = "specialist request\n" + json.dumps({"task_request": {"task": "private"}})
    with pytest.raises(PermissionError, match="private or restricted"):
        prepared["specialists"]["backend_api"]["invoker"](private_prompt, 30)


def test_profile_tool_scopes_must_exist_in_host_approved_registry(tmp_path: Path):
    path = tmp_path / "roles.json"
    document = create_provider_role_profile_document(model_id="qwen2.5-coder:14b")
    document["roles"]["backend_api"]["allowed_tools"] = ["repository_write"]
    write_provider_role_profile(path, document, apply=True)
    with pytest.raises(ProviderRoleProfileError, match="absent from the host-approved registry"):
        build_profiled_crew_adapter_inputs(path, tools=ApprovedToolRegistry(), environ={})
