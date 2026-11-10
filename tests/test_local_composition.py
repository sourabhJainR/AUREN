from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from portable.adaptive_trigger import AdaptiveTriggerRequest
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
    with patch(
        "portable.local_host.canonical_auren_home",
        return_value=token_path.parent / "auren-home",
    ):
        return create_local_mcp_service(
            composition.runtime,
            project,
            port,
            token_path=token_path,
            composition=composition,
        )[0]


class LocalRuntimeCompositionTests(unittest.TestCase):
    def test_local_mcp_submission_recovers_after_host_restart_without_duplicate_provider_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "workspace"
            _git_workspace(project)
            provider = FakeProvider()
            task = "Review the repository architecture and return a bounded implementation plan."
            arguments = {
                "task": task,
                "idempotency_key": "local-mcp-restart-test-0001",
            }
            with patch.dict(os.environ, {
                "AUREN_LOCAL_REMOTE_INFERENCE": "enabled",
                "AUREN_LOCAL_DATA_CLASSIFICATION": "public",
            }):
                state_root = root / "local-state"
                token_path = root / "credentials" / "local-operator.token"

                first = create_local_composition(project, state_root=state_root, provider_pool=provider)
                first.trigger = DeferredTrigger(first.trigger)
                service1 = _service(first, project, token_path, 43141)
                token = token_path.read_text(encoding="utf-8").strip()
                principal1 = service1.token_verifier.verify(token)
                receipt1 = service1.call_tool("auren.submit_task", arguments, principal1)
                self.assertEqual(receipt1["status"], "accepted")
                task_id = receipt1["task_id"]
                self.assertEqual(provider.calls, [])

                service1.close()
                first.close()

                second = create_local_composition(project, state_root=state_root, provider_pool=provider)
                second.trigger = DeferredTrigger(second.trigger)
                service2 = _service(second, project, token_path, 43142)
                principal2 = service2.token_verifier.verify(token)
                receipt2 = service2.call_tool("auren.submit_task", arguments, principal2)
                self.assertEqual(receipt2["task_id"], task_id)
                self.assertTrue(second.trigger.dispatch_once(limit=1))
                event = second.runtime.trigger_runtime.get_event(task_id)
                self.assertIsNotNone(event)
                self.assertEqual(event.status, "success")
                self.assertEqual(event.outcome.get("result_status"), "completed")
                self.assertTrue(event.outcome.get("evidence_refs"))
                self.assertTrue(event.outcome.get("controller_task_id"))

                controller_task = second.controller.task_by_work_key("local-mcp:" + task_id)
                self.assertIsNotNone(controller_task)
                self.assertEqual(controller_task.state, "completed")
                self.assertTrue(second.supervisor.verify_audit_chain())
                plan, run = second.recovery_bridge.load_verified("local-" + task_id)
                self.assertTrue(plan.source_revision)
                self.assertEqual(run.tasks[0].status, "passed")
                self.assertTrue(run.tasks[0].evidence_refs)
                self.assertEqual(len(provider.calls), 1)

                service2.close()
                second.close()

                third = create_local_composition(project, state_root=state_root, provider_pool=provider)
                try:
                    plan_after_restart, run_after_restart = third.recovery_bridge.load_verified("local-" + task_id)
                    self.assertEqual(plan_after_restart.digest, plan.digest)
                    self.assertEqual(run_after_restart.tasks[0].status, "passed")
                    event_after_restart = third.runtime.trigger_runtime.get_event(task_id)
                    self.assertIsNotNone(event_after_restart)
                    replay = third.run_trigger(
                        AdaptiveTriggerRequest(
                            task=task,
                            project_root=str(project),
                            context=event_after_restart.payload["context"],
                        ),
                        task_id,
                    )
                    self.assertEqual(replay.status, "completed")
                    self.assertEqual(replay.evidence_refs, run_after_restart.tasks[0].evidence_refs)
                    self.assertEqual(len(provider.calls), 1)
                finally:
                    third.close()

    def test_local_mcp_rejects_task_admission_when_no_provider_is_configured(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "workspace"
            _git_workspace(project)
            with patch("portable.local_composition.provider_pool_from_environment", return_value=None):
                composition = create_local_composition(project, state_root=root / "state")
            service = _service(composition, project, root / "token", 43143)
            try:
                principal = service.token_verifier.verify((root / "token").read_text(encoding="utf-8").strip())
                with self.assertRaisesRegex(Exception, "no provider is configured"):
                    service.call_tool(
                        "auren.submit_task",
                        {"task": "Inspect this repository.", "idempotency_key": "local-no-provider-test-0001"},
                        principal,
                    )
                self.assertEqual(
                    composition.runtime.trigger_runtime.list_events(kind="adaptive_runtime", limit=10), [],
                )
            finally:
                service.close()
                composition.close()

    def test_provider_operation_ledger_never_replays_an_ambiguous_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger_path = Path(temporary) / "provider-operations.sqlite3"
            ledger = LocalProviderOperationLedger(ledger_path)
            key = "local-mcp:stable-operation-key-0001"
            self.assertIsNone(ledger.begin(key))
            with self.assertRaisesRegex(LocalProviderOutcomeUnknown, "automatic replay is blocked"):
                LocalProviderOperationLedger(ledger_path).begin(key)

    def test_remote_provider_requires_explicit_public_workspace_opt_in(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "workspace"
            _git_workspace(project)
            with patch.dict(os.environ, {
                "AUREN_LOCAL_REMOTE_INFERENCE": "",
                "AUREN_LOCAL_DATA_CLASSIFICATION": "",
            }):
                provider = FakeProvider()
                composition = create_local_composition(
                    project, state_root=root / "state", provider_pool=provider,
                )
                service = _service(composition, project, root / "token", 43144)
                try:
                    principal = service.token_verifier.verify((root / "token").read_text(encoding="utf-8").strip())
                    with self.assertRaisesRegex(Exception, "remote inference is disabled by default"):
                        service.call_tool(
                            "auren.submit_task",
                            {"task": "Summarize this public sample.", "idempotency_key": "local-public-optin-test-0001"},
                            principal,
                        )
                    self.assertEqual(
                        composition.runtime.trigger_runtime.list_events(kind="adaptive_runtime", limit=10), [],
                    )
                    self.assertEqual(provider.calls, [])
                finally:
                    service.close()
                    composition.close()

    def test_local_operator_approval_authority_is_role_and_scope_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "workspace"
            _git_workspace(project)
            composition = create_local_composition(
                project, state_root=root / "state", provider_pool=FakeProvider(),
            )
            try:
                authorize = composition.operator.authorize_approver
                self.assertTrue(authorize(
                    "local-operator", "engineering_manager", "repo:write", ("engineering_manager",),
                ))
                self.assertFalse(authorize(
                    "BE-01", "engineering_manager", "repo:write", ("engineering_manager",),
                ))
                self.assertFalse(authorize(
                    "local-operator", "engineering_manager", "global:admin", ("engineering_manager",),
                ))
                self.assertFalse(authorize(
                    "local-operator", "release_manager", "repo:write", ("release_manager",),
                ))
            finally:
                composition.close()


if __name__ == "__main__":
    unittest.main()
