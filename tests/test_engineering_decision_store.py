from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.engineering_decision_store import EngineeringDecisionStore
from portable.engineering_plan import EngineeringPlan, PlanTask, new_plan_run
from portable.engineering_plan_store import EngineeringPlanStore, PlanRevisionConflict


class EngineeringDecisionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "plans.sqlite3"
        self.plans = EngineeringPlanStore(self.path)
        self.plan = EngineeringPlan(
            run_id="decision-run",
            goal="Deliver a research application",
            requirements=("Show source-backed market data", "Do not publish restricted provider data"),
            source_revision="a" * 40,
            tasks=(
                PlanTask("data", "Integrate data sources", ("provenance is present",), "revert data integration"),
                PlanTask("ui", "Build the user interface", ("stale data is visible",), "revert UI", depends_on=("data",)),
            ),
        )
        self.run = self.plans.create(self.plan)
        self.decisions = EngineeringDecisionStore(self.plans, actor_authorizer=lambda _decision: "user:test")

    def _record(self, **overrides):
        values = {
            "run_id": self.plan.run_id,
            "decision_id": "provider-licensing",
            "expected_revision": 0,
            "plan_digest": self.plan.digest,
            "run_revision": self.run.revision,
            "decision_type": "conflict_resolution",
            "question": "May provider data be redistributed publicly?",
            "options": ("Use a licensed provider", "Use non-redistributable data internally"),
            "selected_option": "Use a licensed provider",
            "rationale": "Public redistribution requires explicit rights.",
            "rejected_alternatives": (("Use non-redistributable data internally", "It conflicts with public distribution."),),
            "affected_task_ids": ("data", "ui"),
            "actor_id": "user:test",
        }
        values.update(overrides)
        return self.decisions.record(**values)

    def test_decision_actor_is_host_authenticated_and_caller_spoof_is_rejected(self):
        with self.assertRaisesRegex(PermissionError, "does not match"):
            self._record(actor_id="user:spoof")

    def test_record_is_bound_to_exact_plan_and_run_revision(self):
        receipt = self._record()
        self.assertEqual(self.plan.digest, receipt.plan_digest)
        self.assertEqual(self.run.revision, receipt.run_revision)
        self.assertTrue(receipt.current_binding)
        self.assertEqual(receipt.record_digest, self.decisions.latest(self.plan.run_id, "provider-licensing").record_digest)

    def test_changed_decision_appends_version_and_links_prior_digest(self):
        first = self._record()
        second = self._record(
            expected_revision=1,
            selected_option="Use non-redistributable data internally",
            rationale="The initial product scope is internal-only.",
            rejected_alternatives=(("Use a licensed provider", "Paid licensing is deferred for the MVP."),),
        )
        self.assertEqual(1, first.revision)
        self.assertEqual(2, second.revision)
        self.assertEqual(first.record_digest, second.supersedes_digest)
        self.assertEqual(2, len(self.decisions.history(self.plan.run_id, "provider-licensing")))

    def test_retry_after_commit_is_idempotent(self):
        first = self._record()
        retry = self._record(expected_revision=0)
        self.assertEqual(first.record_digest, retry.record_digest)
        self.assertEqual(1, len(self.decisions.history(self.plan.run_id, "provider-licensing")))

    def test_stale_plan_run_revision_blocks_new_decision(self):
        self._record()
        next_run = self.run.transition_task("data", "ready", plan=self.plan, reason="begin work")
        self.plans.save_run(self.plan.run_id, next_run, expected_revision=0)
        with self.assertRaises(PlanRevisionConflict):
            self._record(expected_revision=1, run_revision=0)
        historical = self.decisions.history(self.plan.run_id, "provider-licensing")
        self.assertEqual(1, len(historical))
        self.assertFalse(historical[0].current_binding)

    def test_unknown_affected_task_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown plan tasks"):
            self._record(affected_task_ids=("not-a-plan-task",))

    def test_decision_options_and_rejected_alternatives_are_validated(self):
        with self.assertRaisesRegex(ValueError, "selected_option"):
            self._record(selected_option="Unlisted option")
        with self.assertRaisesRegex(ValueError, "rejected alternatives"):
            self._record(rejected_alternatives=(("Use a licensed provider", "cannot reject selected"),))

    def test_history_detects_row_identity_tampering(self):
        self._record()
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE engineering_decision_records SET decision_id=? WHERE run_id=? AND decision_id=?",
                ("renamed-decision", self.plan.run_id, "provider-licensing"),
            )
        with self.assertRaisesRegex(ValueError, "identity or binding mismatch"):
            self.decisions.history(self.plan.run_id, "renamed-decision")

    def test_decision_payload_has_bounded_option_and_task_counts(self):
        with self.assertRaisesRegex(ValueError, "between two and twenty options"):
            self._record(options=tuple(f"option-{index}" for index in range(21)))
        with self.assertRaisesRegex(ValueError, "one hundred affected task IDs"):
            self._record(affected_task_ids=tuple(f"task-{index}" for index in range(101)))

    def test_history_detects_broken_supersession_chain(self):
        first = self._record()
        self._record(
            expected_revision=1,
            selected_option="Use non-redistributable data internally",
            rationale="MVP scope changed.",
            rejected_alternatives=(("Use a licensed provider", "Not selected for MVP."),),
        )
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE engineering_decision_records SET record_json=? WHERE run_id=? AND decision_id=? AND revision=2",
                ('{"tampered":true}', self.plan.run_id, "provider-licensing"),
            )
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.decisions.history(self.plan.run_id, "provider-licensing")


if __name__ == "__main__":
    unittest.main()
