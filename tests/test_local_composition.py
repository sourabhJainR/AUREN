from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import sys
import time
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
                second.runtime.trigger_runtime.retry_delay_seconds = 0
                original_complete = second.runtime.trigger_runtime.complete
                failed_terminal_ack = {"value": False}

                def lose_first_terminal_ack(event_id, claim_id, status, detail="", **kwargs):
                    if status == "success" and not failed_terminal_ack["value"]:
                        failed_terminal_ack["value"] = True
                        raise RuntimeError("simulated interruption after durable controller completion")
                    return original_complete(event_id, claim_id, status, detail, **kwargs)

                second.runtime.trigger_runtime.complete = lose_first_terminal_ack
                try:
                    second.trigger.dispatch_once(limit=1)
                finally:
                    second.runtime.trigger_runtime.complete = original_complete
                event = second.runtime.trigger_runtime.get_event(task_id)
                self.assertIsNotNone(event)
                self.assertEqual(event.status, "pending")
                self.assertTrue(failed_terminal_ack["value"])
                self.assertEqual(event.outcome.get("error_type"), "RuntimeError")

                plan, run = second.recovery_bridge.load_verified("local-" + task_id)
                reconciler = next(iter(second.supervisor.goal_reconcilers.values()))
                work_key = reconciler.work_key(plan.tasks[0], run.tasks[0].replan_generation)
                controller_task = second.controller.task_by_work_key(work_key)
                self.assertIsNotNone(controller_task)
                self.assertEqual(controller_task.state, "completed")
                self.assertTrue(second.supervisor.verify_audit_chain())
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
                    self.assertEqual(event_after_restart.status, "pending")
                    self.assertTrue(third.trigger.dispatch_once(limit=1))
                    event_after_recovery = third.runtime.trigger_runtime.get_event(task_id)
                    self.assertIsNotNone(event_after_recovery)
                    self.assertEqual(event_after_recovery.status, "success")
                    self.assertEqual(event_after_recovery.outcome.get("result_status"), "completed")
                    self.assertTrue(event_after_recovery.outcome.get("evidence_refs"))
                    self.assertTrue(event_after_recovery.outcome.get("controller_task_id"))
                    service3 = _service(third, project, token_path, 43145)
                    try:
                        principal3 = service3.token_verifier.verify(token)
                        mcp_summary = service3.call_tool(
                            "auren.get_task", {"task_id": task_id}, principal3,
                        )
                        self.assertEqual(mcp_summary["status"], "completed")
                        self.assertTrue(mcp_summary["completion_verified"])
                        self.assertTrue(mcp_summary["outcome"]["evidence_refs"])
                    finally:
                        service3.close()
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

    def test_local_mcp_recovers_after_real_process_death_without_replaying_provider(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "workspace"
            _git_workspace(project)
            state_root = root / "local-state"
            token_path = root / "credentials" / "local-operator.token"
            counter_path = root / "provider-calls.txt"
            task = "Review the repository architecture and return a bounded implementation plan."
            script = root / "crash_worker.py"
            script.write_text(
                """
import os
import sys
from pathlib import Path
from tests.test_local_composition import FakeProvider, DeferredTrigger, _service
from portable.local_composition import create_local_composition

project, state_root, token_path, counter_path, task = map(Path, sys.argv[1:6])
class CountingProvider(FakeProvider):
    def __call__(self, request):
        result = super().__call__(request)
        with counter_path.open("a", encoding="utf-8") as stream:
            stream.write("provider-dispatch\\n")
            stream.flush()
            os.fsync(stream.fileno())
        return result

composition = create_local_composition(
    project, state_root=state_root, provider_pool=CountingProvider(),
)
composition.runtime.trigger_runtime.claim_lease_seconds = 1
composition.trigger = DeferredTrigger(composition.trigger)
service = _service(composition, project, token_path, 43147)
token = token_path.read_text(encoding="utf-8").strip()
principal = service.token_verifier.verify(token)
receipt = service.call_tool(
    "auren.submit_task",
    {"task": task.read_text(encoding="utf-8"), "idempotency_key": "local-process-crash-0001"},
    principal,
)
if receipt.get("status") != "accepted":
    raise SystemExit(70)
counter_path.with_suffix(".task-id").write_text(receipt["task_id"], encoding="utf-8")
original_complete = composition.runtime.trigger_runtime.complete
def crash_after_controller_completion(event_id, claim_id, status, detail="", **kwargs):
    if status == "success":
        os._exit(86)
    return original_complete(event_id, claim_id, status, detail, **kwargs)
composition.runtime.trigger_runtime.complete = crash_after_controller_completion
composition.trigger.dispatch_once(limit=1)
os._exit(87)
""",
                encoding="utf-8",
            )
            task_file = root / "task.txt"
            task_file.write_text(task, encoding="utf-8")
            environment = dict(os.environ)
            environment["AUREN_LOCAL_REMOTE_INFERENCE"] = "enabled"
            environment["AUREN_LOCAL_DATA_CLASSIFICATION"] = "public"
            repository_root = Path(__file__).resolve().parents[1]
            existing_pythonpath = environment.get("PYTHONPATH", "")
            environment["PYTHONPATH"] = str(repository_root) + (
                os.pathsep + existing_pythonpath if existing_pythonpath else ""
            )
            completed = subprocess.run(
                [
                    sys.executable, str(script), str(project), str(state_root),
                    str(token_path), str(counter_path), str(task_file),
                ],
                cwd=repository_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(completed.returncode, 86, completed.stdout + completed.stderr)
            self.assertEqual(counter_path.read_text(encoding="utf-8").splitlines(), ["provider-dispatch"])

            with patch.dict(os.environ, {
                "AUREN_LOCAL_REMOTE_INFERENCE": "enabled",
                "AUREN_LOCAL_DATA_CLASSIFICATION": "public",
            }):
                provider_after_restart = FakeProvider()
                second = create_local_composition(
                    project, state_root=state_root, provider_pool=provider_after_restart,
                )
                second.runtime.trigger_runtime.claim_lease_seconds = 1
                second.runtime.trigger_runtime.retry_delay_seconds = 1
                try:
                    time.sleep(1.2)
                    task_id = counter_path.with_suffix(".task-id").read_text(encoding="utf-8").strip()
                    before_recovery = second.runtime.trigger_runtime.get_event(task_id)
                    due_before_recovery = second.runtime.trigger_runtime.due(kind="adaptive_runtime")
                    recovered = second.trigger.dispatch_once(limit=1)
                    event = second.runtime.trigger_runtime.get_event(task_id)
                    self.assertTrue(
                        recovered,
                        f"event_before={before_recovery!r}; due_before={due_before_recovery!r}; "
                        f"event_after={event!r}",
                    )
                    self.assertIsNotNone(event)
                    self.assertEqual(event.status, "success")
                    self.assertEqual(event.outcome.get("result_status"), "completed")
                    self.assertEqual(provider_after_restart.calls, [])
                    self.assertEqual(counter_path.read_text(encoding="utf-8").splitlines(), ["provider-dispatch"])
                    service = _service(second, project, token_path, 43148)
                    try:
                        token = token_path.read_text(encoding="utf-8").strip()
                        principal = service.token_verifier.verify(token)
                        summary = service.call_tool(
                            "auren.get_task",
                            {"task_id": task_id},
                            principal,
                        )
                        self.assertEqual(summary["status"], "completed")
                        self.assertTrue(summary["completion_verified"])
                        self.assertTrue(summary["outcome"]["evidence_refs"])
                    finally:
                        service.close()
                    self.assertTrue(second.supervisor.verify_audit_chain())
                finally:
                    second.close()

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
                    composition.runtime.trigger_runtime.list_events(kind="adaptive_runtime", limit=10), (),
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
                        composition.runtime.trigger_runtime.list_events(kind="adaptive_runtime", limit=10), (),
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
