"""AdaptiveRuntime wiring tests for project-scoped durable routines."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from portable.adaptive_runtime import AdaptiveRuntime
from portable.durable_routines import RoutineSpec
from portable.orchestration import Graph


class AdaptiveRuntimeRoutineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state_root = root / "state"
        self.project = root / "repo-one"
        self.other_project = root / "repo-two"
        self.project.mkdir()
        self.other_project.mkdir()
        self.runtime = AdaptiveRuntime(Graph([]), state_root=self.state_root)
        self.submissions = []

        def submit(handler, payload, **kwargs):
            task_id = f"task-{len(self.submissions) + 1}"
            self.submissions.append((handler, dict(payload), dict(kwargs), task_id))
            return {"id": task_id}

        self.runtime.execution_controller.submit = submit
        self.runtime.execution_controller.task_by_work_key = lambda _work_key: None

    @staticmethod
    def spec(**overrides):
        values = {
            "routine_id": "repository-health",
            "version": 1,
            "handler": "repository_health_check",
            "payload": {"repository": "owner/repo"},
            "trigger": "manual",
            "enabled": True,
            "deadline_seconds": 900,
        }
        values.update(overrides)
        return RoutineSpec(**values)

    def test_manual_dispatch_uses_controller_and_is_project_scoped(self):
        spec = self.spec()
        self.runtime.register_routine(self.project, spec)

        result = self.runtime.dispatch_manual_routine(
            self.project, spec.routine_id, invocation_id="operator-run-1",
        )

        self.assertEqual("submitted", result.status)
        self.assertEqual(1, len(self.submissions))
        handler, payload, kwargs, task_id = self.submissions[0]
        self.assertEqual(spec.handler, handler)
        self.assertEqual(result.work_key, kwargs["work_key"])
        self.assertEqual(spec.goal, kwargs["goal"])
        self.assertEqual(spec.deadline_seconds, kwargs["deadline_seconds"])
        self.assertEqual("owner/repo", payload["repository"])
        self.assertEqual(task_id, result.task_id)
        with self.assertRaises(KeyError):
            self.runtime.routine_registry(self.other_project).get(spec.routine_id)

    def test_webhook_dispatch_requires_host_verifier_and_uses_controller(self):
        spec = self.spec(trigger="webhook", event_type="push")
        self.runtime.register_routine(self.project, spec)
        authenticated = []

        def verify(routine_id, event_id, payload):
            authenticated.append((routine_id, event_id, dict(payload)))
            return True

        result = self.runtime.dispatch_webhook_routine(
            self.project, spec.routine_id,
            event_id="provider-event-1",
            event_type="push",
            payload={"ref": "refs/heads/main"},
            authenticate=verify,
        )

        self.assertEqual("submitted", result.status)
        self.assertEqual(
            (spec.routine_id, "provider-event-1", {"ref": "refs/heads/main"}),
            authenticated[0],
        )
        self.assertEqual(
            {"ref": "refs/heads/main"},
            self.submissions[0][1]["_auren_trigger_event"],
        )

    def test_due_interval_dispatch_is_bounded_and_uses_canonical_controller(self):
        spec = self.spec(trigger="interval", interval_seconds=60)
        self.runtime.register_routine(self.project, spec)
        now = datetime.now(timezone.utc) + timedelta(minutes=5)

        results = self.runtime.dispatch_due_routines(
            self.project, now=now, limit=1,
        )

        self.assertEqual(1, len(results))
        self.assertEqual("submitted", results[0].status)
        self.assertEqual(results[0].work_key, self.submissions[0][2]["work_key"])

    def test_uncertain_controller_submit_reconciles_by_stable_work_key(self):
        spec = self.spec()
        self.runtime.register_routine(self.project, spec)
        calls = []

        def uncertain_submit(*_args, **_kwargs):
            calls.append("submit")
            raise TimeoutError("acknowledgement lost")

        self.runtime.execution_controller.submit = uncertain_submit
        self.runtime.execution_controller.task_by_work_key = (
            lambda work_key: {"id": "original-task"} if work_key else None
        )

        first = self.runtime.dispatch_manual_routine(
            self.project, spec.routine_id, invocation_id="operator-run-uncertain",
        )
        recovered = self.runtime.dispatch_manual_routine(
            self.project, spec.routine_id, invocation_id="operator-run-uncertain",
        )

        self.assertEqual("unknown", first.status)
        self.assertEqual("submitted", recovered.status)
        self.assertEqual("original-task", recovered.task_id)
        self.assertEqual(["submit"], calls)


    def test_cancel_routine_occurrences_uses_durable_controller_api(self):
        spec = self.spec()
        self.runtime.register_routine(self.project, spec)
        submitted = self.runtime.dispatch_manual_routine(
            self.project, spec.routine_id, invocation_id="operator-run-cancel",
        )
        self.runtime.execution_controller.task_by_work_key = lambda _key: {
            "id": submitted.task_id, "state": "running",
        }
        requests = []
        self.runtime.execution_controller.request_cancellation = (
            lambda task_id, *, reason: requests.append((task_id, reason)) or True
        )

        results = self.runtime.cancel_routine_occurrences(
            self.project, spec.routine_id, reason="stop requested",
        )

        self.assertEqual("cancellation_requested", results[0].status)
        self.assertEqual([(submitted.task_id, "stop requested")], requests)
        occurrence = self.runtime.routine_registry(self.project).occurrence(
            submitted.occurrence_key,
        )
        self.assertEqual("cancellation_requested", occurrence["status"])


if __name__ == "__main__":
    unittest.main()
