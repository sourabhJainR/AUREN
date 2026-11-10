from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from portable.local_composition import (
    LocalProviderOperationLedger,
    LocalProviderOutcomeUnknown,
    create_local_composition,
)
from portable.local_host import create_local_mcp_service


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.providers = [SimpleNamespace(
            spec=SimpleNamespace(name="test-provider"),
            model="test-model",
        )]

    def __call__(self, request):
        self.calls.append(request.task)
        content = "A bounded provider response for the submitted local task."
        return SimpleNamespace(
            content=content,
            provider="test-provider",
            model="test-model",
            usage={"input_tokens": 12, "output_tokens": 9},
            response_digest=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )


class DeferredTrigger:
    """Use the real durable trigger but hold dispatch to simulate process restart."""

    def __init__(self, delegate):
        self.delegate = delegate

    def trigger_adaptive_runtime(self, *args, **kwargs):
        kwargs["fire_and_forget"] = False
        return self.delegate.trigger_adaptive_runtime(*args, **kwargs)

    def dispatch_once(self, **kwargs):
        return self.delegate.dispatch_once(**kwargs)

    def close(self, **kwargs):
        return self.delegate.close(**kwargs)


def _git_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True, timeout=10)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "local-test@example.invalid"],
                   check=True, capture_output=True, timeout=10)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "AUREN local test"],
                   check=True, capture_output=True, timeout=10)
    (path / "README.md").write_text("# Local test workspace\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True, timeout=10)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial workspace"],
                   check=True, capture_output=True, timeout=10)


def _service(composition, project: Path, token_path: Path, port: int):
    from unittest.mock import patch
    with patch("portable.local_host.canonical_auren_home", return_value=token_path.parent / "auren-home"):
        return create_local_mcp_service(
            composition.runtime,
            project,
            port,
            token_path=token_path,
            composition=composition,
        )[0]


def test_local_mcp_submission_recovers_after_host_restart_without_factory_or_duplicate_provider_call(tmp_path: Path):
    project = tmp_path / "workspace"
    _git_workspace(project)
    state_root = tmp_path / "local-state"
    token_path = tmp_path / "credentials" / "local-operator.token"
    provider = FakeProvider()
    task = "Review the repository architecture and return a bounded implementation plan."
    arguments = {
        "task": task,
        "idempotency_key": "local-mcp-restart-test-0001",
    }

    first = create_local_composition(project, state_root=state_root, provider_pool=provider)
    first.trigger = DeferredTrigger(first.trigger)
    service1 = _service(first, project, token_path, 43141)
    token = token_path.read_text(encoding="utf-8").strip()
    principal1 = service1.token_verifier.verify(token)
    receipt1 = service1.call_tool("auren.submit_task", arguments, principal1)
    assert receipt1["status"] == "accepted"
    task_id = receipt1["task_id"]
    assert provider.calls == []

    service1.close()
    first.close()

    second = create_local_composition(project, state_root=state_root, provider_pool=provider)
    second.trigger = DeferredTrigger(second.trigger)
    service2 = _service(second, project, token_path, 43142)
    principal2 = service2.token_verifier.verify(token)
    receipt2 = service2.call_tool("auren.submit_task", arguments, principal2)
    assert receipt2["task_id"] == task_id
    assert second.trigger.dispatch_once(limit=1)
    event = second.runtime.trigger_runtime.get_event(task_id)
    assert event is not None and event.status == "success"

    controller_task = second.controller.task_by_work_key("local-mcp:" + task_id)
    assert controller_task is not None and controller_task.state == "completed"
    assert second.supervisor.verify_audit_chain()
    plan, run = second.recovery_bridge.load_verified("local-" + task_id)
    assert plan.source_revision
    assert run.tasks[0].status == "passed"
    assert run.tasks[0].evidence_refs
    assert len(provider.calls) == 1

    service2.close()
    second.close()

    third = create_local_composition(project, state_root=state_root, provider_pool=provider)
    try:
        plan_after_restart, run_after_restart = third.recovery_bridge.load_verified("local-" + task_id)
        assert plan_after_restart.digest == plan.digest
        assert run_after_restart.tasks[0].status == "passed"
        assert len(provider.calls) == 1
    finally:
        third.close()


def test_local_mcp_rejects_task_admission_when_no_provider_is_configured(tmp_path: Path):
    project = tmp_path / "workspace"
    _git_workspace(project)
    from unittest.mock import patch
    with patch("portable.local_composition.provider_pool_from_environment", return_value=None):
        composition = create_local_composition(project, state_root=tmp_path / "state")
    service = _service(composition, project, tmp_path / "token", 43143)
    try:
        principal = service.token_verifier.verify((tmp_path / "token").read_text(encoding="utf-8").strip())
        with pytest.raises(Exception, match="no provider is configured"):
            service.call_tool(
                "auren.submit_task",
                {"task": "Inspect this repository.", "idempotency_key": "local-no-provider-test-0001"},
                principal,
            )
        assert composition.runtime.trigger_runtime.list_events(kind="adaptive_runtime", limit=10) == []
    finally:
        service.close()
        composition.close()


def test_provider_operation_ledger_never_replays_an_ambiguous_dispatch(tmp_path: Path):
    ledger = LocalProviderOperationLedger(tmp_path / "provider-operations.sqlite3")
    assert ledger.begin("local-mcp:stable-operation-key-0001") is None
    with pytest.raises(LocalProviderOutcomeUnknown, match="automatic replay is blocked"):
        LocalProviderOperationLedger(tmp_path / "provider-operations.sqlite3").begin(
            "local-mcp:stable-operation-key-0001",
        )
