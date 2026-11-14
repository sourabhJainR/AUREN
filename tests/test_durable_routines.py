"""Contract tests for durable, idempotent routine trigger receipts."""
from __future__ import annotations

import json
from contextlib import closing

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from portable.durable_routines import DurableRoutineRegistry, RoutineSpec


class DurableRoutineRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = DurableRoutineRegistry(Path(self.tmp.name) / "routines.sqlite")
        self.submissions = []

    def submit(self, handler, payload, **kwargs):
        task_id = f"task-{len(self.submissions) + 1}"
        self.submissions.append((handler, dict(payload), kwargs, task_id))
        return {"id": task_id}

    def spec(self, **overrides):
        values = {
            "routine_id": "repo-health",
            "version": 1,
            "handler": "repo_health_check",
            "payload": {"repository": "owner/repo"},
            "trigger": "webhook",
            "event_type": "push",
            "enabled": True,
        }
        values.update(overrides)
        return RoutineSpec(**values)

    def test_registration_is_idempotent_and_same_version_mutation_is_rejected(self):
        spec = self.spec()
        self.assertEqual(spec.digest, self.registry.register(spec))
        self.assertEqual(spec.digest, self.registry.register(spec))
        with self.assertRaisesRegex(ValueError, "same routine version"):
            self.registry.register(self.spec(payload={"repository": "other/repo"}))

    def test_utc_timezone_is_supported_without_platform_tzdata(self):
        self.assertEqual("UTC", self.spec().timezone)

    def test_unknown_timezone_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown routine timezone"):
            self.spec(timezone="Mars/Olympus_Mons")

    def test_one_time_schedule_rejects_naive_timestamp(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.spec(
                trigger="once", event_type=None,
                run_at="2026-11-07T10:00:00",
            )

    def test_version_cannot_move_backwards(self):
        self.registry.register(self.spec(version=2))
        with self.assertRaisesRegex(ValueError, "cannot move backwards"):
            self.registry.register(self.spec(version=1))

    def test_webhook_requires_authentication_and_deduplicates_event_id(self):
        spec = self.spec()
        self.registry.register(spec)
        with self.assertRaises(PermissionError):
            self.registry.dispatch_webhook(
                spec.routine_id, event_id="evt-1", event_type="push", payload={},
                authenticate=lambda *_: False, submit=self.submit,
            )
        first = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-1", event_type="push",
            payload={"ref": "refs/heads/main"},
            authenticate=lambda *_: True, submit=self.submit,
        )
        second = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-1", event_type="push",
            payload={"ref": "refs/heads/main"},
            authenticate=lambda *_: True, submit=self.submit,
        )
        self.assertEqual("submitted", first.status)
        self.assertEqual("duplicate", second.status)
        self.assertEqual(1, len(self.submissions))
        self.assertEqual(first.task_id, second.task_id)

    def test_webhook_event_id_cannot_be_reused_with_different_payload(self):
        spec = self.spec()
        self.registry.register(spec)
        self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-collision", event_type="push",
            payload={"ref": "refs/heads/main"},
            authenticate=lambda *_: True, submit=self.submit,
        )
        with self.assertRaisesRegex(ValueError, "reused with different payload"):
            self.registry.dispatch_webhook(
                spec.routine_id, event_id="evt-collision", event_type="push",
                payload={"ref": "refs/heads/release"},
                authenticate=lambda *_: True, submit=self.submit,
            )
        self.assertEqual(1, len(self.submissions))

    def test_manual_invocations_are_distinct_but_idempotent_by_invocation_id(self):
        spec = self.spec(trigger="manual", event_type=None)
        self.registry.register(spec)
        first = self.registry.dispatch_manual(
            spec.routine_id, invocation_id="operator-run-1", submit=self.submit,
        )
        duplicate = self.registry.dispatch_manual(
            spec.routine_id, invocation_id="operator-run-1", submit=self.submit,
        )
        second = self.registry.dispatch_manual(
            spec.routine_id, invocation_id="operator-run-2", submit=self.submit,
        )
        self.assertEqual("submitted", first.status)
        self.assertEqual("duplicate", duplicate.status)
        self.assertEqual("submitted", second.status)
        self.assertEqual(2, len(self.submissions))

    def test_webhook_payload_must_be_bounded_json(self):
        spec = self.spec()
        self.registry.register(spec)
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            self.registry.dispatch_webhook(
                spec.routine_id, event_id="evt-2", event_type="push",
                payload={"bad": object()}, authenticate=lambda *_: True,
                submit=self.submit,
            )

    def test_webhook_authenticator_cannot_mutate_authenticated_payload(self):
        spec = self.spec()
        self.registry.register(spec)

        def authenticate(_routine_id, _event_id, authenticated_payload):
            authenticated_payload["ref"] = "refs/heads/attacker"
            return True

        result = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-snapshot", event_type="push",
            payload={"ref": "refs/heads/main"},
            authenticate=authenticate, submit=self.submit,
        )
        self.assertEqual("submitted", result.status)
        self.assertEqual(
            "refs/heads/main",
            self.submissions[0][1]["_auren_trigger_event"]["ref"],
        )

    def test_new_routines_are_disabled_until_explicitly_enabled(self):
        spec = RoutineSpec(
            routine_id="manual-health",
            version=1,
            handler="repo_health_check",
            payload={"repository": "owner/repo"},
            trigger="manual",
        )
        self.assertFalse(spec.enabled)
        self.registry.register(spec)

        blocked = self.registry.dispatch_manual(
            spec.routine_id, invocation_id="operator-run-disabled", submit=self.submit,
        )
        self.assertEqual("disabled", blocked.status)
        self.assertEqual([], self.submissions)

        self.registry.set_enabled(spec.routine_id, True)
        dispatched = self.registry.dispatch_manual(
            spec.routine_id, invocation_id="operator-run-enabled", submit=self.submit,
        )
        self.assertEqual("submitted", dispatched.status)
        self.assertEqual(1, len(self.submissions))

    def test_approval_gate_blocks_without_creating_occurrence(self):
        spec = self.spec(approval_required=True)
        self.registry.register(spec)
        result = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-3", event_type="push", payload={},
            authenticate=lambda *_: True, submit=self.submit,
            approval=lambda _: False,
        )
        self.assertEqual("blocked", result.status)
        self.assertEqual([], self.submissions)

    def test_uncertain_submit_is_never_replayed_without_authoritative_lookup(self):
        spec = self.spec()
        self.registry.register(spec)

        def uncertain(*args, **kwargs):
            raise TimeoutError("connection lost after dispatch")

        first = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-4", event_type="push", payload={},
            authenticate=lambda *_: True, submit=uncertain,
        )
        second = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-4", event_type="push", payload={},
            authenticate=lambda *_: True, submit=self.submit,
        )
        self.assertEqual("unknown", first.status)
        self.assertEqual("unknown", second.status)
        self.assertIn("reconcile", second.reason)
        self.assertEqual([], self.submissions)

    def test_unknown_submit_can_be_reconciled_without_duplicate_dispatch(self):
        spec = self.spec()
        self.registry.register(spec)

        def uncertain(*args, **kwargs):
            raise TimeoutError("ack lost")

        first = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-5", event_type="push", payload={},
            authenticate=lambda *_: True, submit=uncertain,
        )
        recovered = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-5", event_type="push", payload={},
            authenticate=lambda *_: True, submit=self.submit,
            reconcile=lambda work_key: {"id": "original-task"},
        )
        self.assertEqual("unknown", first.status)
        self.assertEqual("submitted", recovered.status)
        self.assertEqual("original-task", recovered.task_id)
        self.assertEqual([], self.submissions)

    def test_due_one_time_routine_dispatches_once(self):
        due = datetime.now(timezone.utc) - timedelta(minutes=1)
        spec = self.spec(
            trigger="once", event_type=None, run_at=due.isoformat(),
        )
        self.registry.register(spec)
        first = self.registry.dispatch_due(now=datetime.now(timezone.utc), submit=self.submit)
        second = self.registry.dispatch_due(now=datetime.now(timezone.utc), submit=self.submit)
        self.assertEqual(1, len(first))
        self.assertEqual("submitted", first[0].status)
        self.assertEqual((), second)
        self.assertEqual(1, len(self.submissions))

    def test_interval_routine_is_scheduled_after_registration(self):
        spec = self.spec(
            trigger="interval", event_type=None, interval_seconds=60,
        )
        self.registry.register(spec)
        early = self.registry.dispatch_due(
            now=datetime.now(timezone.utc) + timedelta(seconds=30), submit=self.submit,
        )
        due = self.registry.dispatch_due(
            now=datetime.now(timezone.utc) + timedelta(seconds=61), submit=self.submit,
        )
        self.assertEqual((), early)
        self.assertEqual(1, len(due))
        self.assertEqual("submitted", due[0].status)

    def test_approval_block_does_not_consume_scheduled_occurrence(self):
        spec = self.spec(
            trigger="interval", event_type=None, interval_seconds=60,
            approval_required=True,
        )
        self.registry.register(spec)
        now = datetime.now(timezone.utc) + timedelta(seconds=61)
        blocked = self.registry.dispatch_due(
            now=now, submit=self.submit, approval=lambda _: False,
        )
        retried = self.registry.dispatch_due(
            now=now, submit=self.submit, approval=lambda _: True,
        )
        self.assertEqual("blocked", blocked[0].status)
        self.assertEqual("submitted", retried[0].status)
        self.assertEqual(1, len(self.submissions))

    def test_scheduled_unknown_outcome_stays_due_until_reconciled(self):
        spec = self.spec(
            trigger="interval", event_type=None, interval_seconds=60,
        )
        self.registry.register(spec)

        def uncertain(*args, **kwargs):
            raise TimeoutError("ack lost after submit")

        now = datetime.now(timezone.utc) + timedelta(seconds=61)
        first = self.registry.dispatch_due(now=now, submit=uncertain)
        second = self.registry.dispatch_due(
            now=now, submit=self.submit,
            reconcile=lambda work_key: {"id": "already-created-task"},
        )
        self.assertEqual("unknown", first[0].status)
        self.assertEqual("submitted", second[0].status)
        self.assertEqual("already-created-task", second[0].task_id)
        self.assertEqual([], self.submissions)

    def test_interval_downtime_coalesces_missed_occurrences_without_backlog(self):
        spec = self.spec(
            trigger="interval", event_type=None, interval_seconds=60,
        )
        self.registry.register(spec)
        now = datetime.now(timezone.utc) + timedelta(minutes=20)

        dispatched = self.registry.dispatch_due(now=now, submit=self.submit)

        self.assertEqual(1, len(dispatched))
        self.assertEqual("submitted", dispatched[0].status)
        self.assertEqual(1, len(self.submissions))
        payload = self.submissions[0][1]
        self.assertGreater(payload["_auren_coalesced_missed_intervals"], 0)
        occurrence = self.registry.occurrence(dispatched[0].occurrence_key)
        receipt = json.loads(occurrence["receipt_json"])
        self.assertEqual(
            payload["_auren_coalesced_missed_intervals"],
            receipt["coalesced_missed_intervals"],
        )
        with closing(self.registry._connect()) as db:
            next_run_at = db.execute(
                "SELECT next_run_at FROM routines WHERE routine_id=?",
                (spec.routine_id,),
            ).fetchone()["next_run_at"]
        self.assertGreater(datetime.fromisoformat(next_run_at), now)
        self.assertEqual(
            (),
            self.registry.dispatch_due(now=now, submit=self.submit),
        )
        self.assertEqual(1, len(self.submissions))

    def test_interval_unknown_outcome_is_reconciled_before_later_due_slots(self):
        spec = self.spec(
            trigger="interval", event_type=None, interval_seconds=60,
        )
        self.registry.register(spec)
        first_due = datetime.now(timezone.utc) + timedelta(seconds=61)

        def uncertain(*args, **kwargs):
            raise TimeoutError("ack lost after dispatch")

        first = self.registry.dispatch_due(now=first_due, submit=uncertain)
        later = first_due + timedelta(minutes=20)
        still_unknown = self.registry.dispatch_due(now=later, submit=self.submit)

        self.assertEqual("unknown", first[0].status)
        self.assertEqual("unknown", still_unknown[0].status)
        self.assertEqual(first[0].occurrence_key, still_unknown[0].occurrence_key)
        self.assertEqual([], self.submissions)

        recovered = self.registry.dispatch_due(
            now=later,
            submit=self.submit,
            reconcile=lambda work_key: {"id": "original-scheduled-task"},
        )
        self.assertEqual("submitted", recovered[0].status)
        self.assertEqual(first[0].occurrence_key, recovered[0].occurrence_key)
        self.assertEqual("original-scheduled-task", recovered[0].task_id)
        self.assertEqual([], self.submissions)

        occurrence = self.registry.occurrence(recovered[0].occurrence_key)
        receipt = json.loads(occurrence["receipt_json"])
        self.assertGreater(receipt["coalesced_missed_intervals"], 0)
        with closing(self.registry._connect()) as db:
            next_run_at = db.execute(
                "SELECT next_run_at FROM routines WHERE routine_id=?",
                (spec.routine_id,),
            ).fetchone()["next_run_at"]
        self.assertGreater(datetime.fromisoformat(next_run_at), later)
        self.assertEqual((), self.registry.dispatch_due(now=later, submit=self.submit))

    def test_interval_submitted_occurrence_recovers_schedule_advance_after_restart(self):
        spec = self.spec(
            trigger="interval", event_type=None, interval_seconds=60,
        )
        self.registry.register(spec)
        with closing(self.registry._connect()) as db:
            cursor_text = db.execute(
                "SELECT next_run_at FROM routines WHERE routine_id=?",
                (spec.routine_id,),
            ).fetchone()["next_run_at"]
        cursor = datetime.fromisoformat(cursor_text)
        trigger_key = f"{spec.routine_id}:v{spec.version}:{cursor.isoformat(timespec='microseconds')}"

        submitted = self.registry._dispatch(
            spec.routine_id, spec.trigger, trigger_key,
            submit=self.submit, approval=None, reconcile=None,
            scheduled_at=cursor, schedule_cursor=cursor,
            expected_version=spec.version,
        )
        self.assertEqual("submitted", submitted.status)
        self.assertEqual(1, len(self.submissions))

        later = cursor + timedelta(minutes=20)
        recovered = self.registry.dispatch_due(now=later, submit=self.submit)

        self.assertEqual("duplicate", recovered[0].status)
        self.assertEqual(submitted.occurrence_key, recovered[0].occurrence_key)
        self.assertEqual(1, len(self.submissions))
        occurrence = self.registry.occurrence(submitted.occurrence_key)
        receipt = json.loads(occurrence["receipt_json"])
        self.assertGreater(receipt["coalesced_missed_intervals"], 0)
        self.assertTrue(receipt["coalesced_after_submission"])
        with closing(self.registry._connect()) as db:
            next_run_at = db.execute(
                "SELECT next_run_at FROM routines WHERE routine_id=?",
                (spec.routine_id,),
            ).fetchone()["next_run_at"]
        self.assertGreater(datetime.fromisoformat(next_run_at), later)

    def test_receipt_keeps_trigger_digest_after_submission_and_unknown_transition(self):
        spec = self.spec()
        self.registry.register(spec)
        submitted = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-receipt", event_type="push",
            payload={"ref": "refs/heads/main"},
            authenticate=lambda *_: True, submit=self.submit,
        )
        receipt = self.registry.occurrence(submitted.occurrence_key)
        self.assertEqual("submitted", receipt["status"])
        self.assertTrue(json.loads(receipt["receipt_json"])["trigger_digest"])
        duplicate = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-receipt", event_type="push",
            payload={"ref": "refs/heads/main"},
            authenticate=lambda *_: True, submit=self.submit,
        )
        self.assertEqual("duplicate", duplicate.status)

    def test_disabled_routine_does_not_dispatch(self):
        spec = self.spec()
        self.registry.register(spec)
        self.registry.set_enabled(spec.routine_id, False)
        result = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-6", event_type="push", payload={},
            authenticate=lambda *_: True, submit=self.submit,
        )
        self.assertEqual("disabled", result.status)
        self.assertEqual([], self.submissions)


    def test_occurrence_receipt_pins_profile_and_policy_digests(self):
        profile_digest = "a" * 64
        policy_digest = "b" * 64
        spec = self.spec(
            profile_digest=profile_digest,
            policy_digest=policy_digest,
        )
        self.registry.register(spec)

        result = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-pinned-profile", event_type="push",
            payload={"ref": "refs/heads/main"},
            authenticate=lambda *_: True, submit=self.submit,
        )

        occurrence = self.registry.occurrence(result.occurrence_key)
        receipt = json.loads(occurrence["receipt_json"])
        self.assertEqual(profile_digest, receipt["profile_digest"])
        self.assertEqual(policy_digest, receipt["policy_digest"])
        self.assertEqual(spec.digest, receipt["routine_digest"])

    def test_profile_and_policy_digests_must_be_exact_lowercase_sha256(self):
        for field_name, value in (
            ("profile_digest", "A" * 64),
            ("profile_digest", "a" * 63),
            ("policy_digest", "not-a-digest"),
        ):
            with self.subTest(field=field_name, value=value):
                with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
                    self.spec(**{field_name: value})


    def test_skip_overlap_policy_blocks_new_occurrence_while_prior_task_is_active(self):
        spec = self.spec(concurrency_policy="skip")
        self.registry.register(spec)
        first = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-overlap-1", event_type="push",
            payload={"ref": "main"}, authenticate=lambda *_: True, submit=self.submit,
        )
        prior_work_key = first.work_key
        reconciled = []

        def lookup(work_key):
            reconciled.append(work_key)
            return {"id": first.task_id, "state": "running"} if work_key == prior_work_key else None

        second = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-overlap-2", event_type="push",
            payload={"ref": "release"}, authenticate=lambda *_: True,
            submit=self.submit, reconcile=lookup,
        )

        self.assertEqual("skipped_overlap", second.status)
        self.assertEqual([prior_work_key], reconciled)
        self.assertEqual(1, len(self.submissions))
        receipt = self.registry.occurrence(second.occurrence_key)
        self.assertEqual("skipped_overlap", receipt["status"])
        self.assertEqual(prior_work_key, json.loads(receipt["receipt_json"])["overlap_work_key"])

    def test_skip_overlap_policy_allows_dispatch_after_prior_task_is_terminal(self):
        spec = self.spec(concurrency_policy="skip")
        self.registry.register(spec)
        first = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-terminal-1", event_type="push",
            payload={"ref": "main"}, authenticate=lambda *_: True, submit=self.submit,
        )
        second = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-terminal-2", event_type="push",
            payload={"ref": "release"}, authenticate=lambda *_: True,
            submit=self.submit,
            reconcile=lambda key: {"id": first.task_id, "state": "completed"} if key == first.work_key else None,
        )
        self.assertEqual("submitted", second.status)
        self.assertEqual(2, len(self.submissions))

    def test_skip_overlap_policy_fails_closed_without_authoritative_lookup(self):
        spec = self.spec(concurrency_policy="skip")
        self.registry.register(spec)
        self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-no-lookup-1", event_type="push",
            payload={"ref": "main"}, authenticate=lambda *_: True, submit=self.submit,
        )
        second = self.registry.dispatch_webhook(
            spec.routine_id, event_id="evt-no-lookup-2", event_type="push",
            payload={"ref": "release"}, authenticate=lambda *_: True, submit=self.submit,
        )
        self.assertEqual("blocked_overlap", second.status)
        self.assertEqual(1, len(self.submissions))

    def test_invalid_concurrency_policy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "concurrency_policy"):
            self.spec(concurrency_policy="queue")


if __name__ == "__main__":
    unittest.main()
