from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.engineering_dashboard import EngineeringDashboard
from portable.engineering_readiness import (
    BASIC_CYCLE_LIMIT_SECONDS,
    EngineeringBudgetLedger,
    EngineeringReadinessBlocked,
    EngineeringReadinessGate,
    PHASE_LIMIT_SECONDS,
)
from portable.progress_journal import ProgressJournal
from portable.runtime_recovery import ExecutionBudgetExceeded
from portable.task_planner import Task, TaskPlan


READY_AXES = {
    "technology": {
        "status": "ready", "summary": "Required runtime and tools are available.",
        "evidence_refs": ["capability:runtime"], "department": "Platform Engineering",
        "owner_role_id": "ARCH-01",
    },
    "intent": {
        "status": "ready", "summary": "Outcome and acceptance are explicit.",
        "evidence_refs": ["plan:acceptance"], "department": "Engineering Management",
        "owner_role_id": "EM-ENG-01",
    },
    "cognitive": {
        "status": "ready", "summary": "Required context and verification path are present.",
        "evidence_refs": ["context:verified"], "department": "Research Engineering",
        "owner_role_id": "RE-01",
    },
}


class EngineeringReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.journal = ProgressJournal(self.root / ".auren" / "progress.jsonl")
        self.gate = EngineeringReadinessGate(self.journal)

    def tearDown(self):
        self.tmp.cleanup()

    def assess(self, axes=None, *, work_key="work:one", intent="a" * 64):
        return self.gate.assess(
            task_id="plan-task:implement", work_key=work_key,
            intent_digest=intent, repository_snapshot_digest="b" * 64,
            axes=READY_AXES if axes is None else axes,
            context_plan_digest="c" * 64,
        )

    def test_missing_axes_create_typed_durable_gaps_with_department_owners(self):
        assessment = self.assess({})
        self.assertFalse(assessment.ready)
        self.assertEqual({"technology", "intent", "cognitive"}, {axis.axis for axis in assessment.axes if axis.status == "gap"})
        events = self.journal._events()
        gap_rows = [row for row in events if row["event"] == "engineering_gap_recorded"]
        self.assertEqual(3, len(gap_rows))
        for row in gap_rows:
            gap = row["metadata"]
            self.assertEqual("auren-engineering-gap-v1", gap["schema_version"])
            self.assertEqual("open", gap["status"])
            self.assertTrue(gap["department"])
            self.assertTrue(gap["owner_role_id"])
            self.assertTrue(gap["remediation"])
            self.assertTrue(gap["acceptance_criteria"])
            self.assertEqual(64, len(gap["envelope_digest"]))

    def test_ready_axis_requires_evidence_and_unknown_axis_is_rejected(self):
        axes = {key: dict(value) for key, value in READY_AXES.items()}
        axes["technology"]["evidence_refs"] = []
        with self.assertRaisesRegex(ValueError, "requires evidence references"):
            self.assess(axes)
        with self.assertRaisesRegex(ValueError, "unknown readiness axes"):
            self.assess(dict(READY_AXES, cognition={"status": "ready"}))

    def test_assessment_is_idempotent_for_the_same_durable_identity_and_inputs(self):
        first = self.assess({})
        count = len(self.journal._events())
        second = self.assess({})
        self.assertEqual(first.assessment_digest, second.assessment_digest)
        self.assertEqual(count, len(self.journal._events()))

    def test_verifier_rejects_tampered_receipt_without_writing_it_to_the_journal(self):
        assessment = self.assess()
        payload = {
            "work_key": "work:one",
            "_auren_engineering_readiness": assessment.to_dict(),
            "_auren_engineering_readiness_digest": assessment.assessment_digest,
        }
        before = len(self.journal._events())
        tampered = dict(payload)
        receipt = dict(payload["_auren_engineering_readiness"])
        receipt["axes"] = {key: dict(value) for key, value in receipt["axes"].items()}
        receipt["axes"]["technology"]["summary"] = "forged readiness"
        tampered["_auren_engineering_readiness"] = receipt
        with self.assertRaisesRegex(ValueError, "assessment digest mismatch"):
            self.gate.verify_payload(tampered)
        self.assertEqual(before, len(self.journal._events()))

    def test_verifier_binds_receipt_to_stable_work_key_and_envelope(self):
        assessment = self.assess()
        payload = {
            "work_key": "work:one",
            "_auren_engineering_readiness": assessment.to_dict(),
            "_auren_engineering_readiness_digest": assessment.assessment_digest,
        }
        verified = self.gate.verify_payload(payload)
        self.assertEqual(assessment.assessment_digest, verified.assessment_digest)
        wrong_identity = dict(payload, work_key="work:other")
        with self.assertRaisesRegex(ValueError, "work_key does not match"):
            self.gate.verify_payload(wrong_identity)
        wrong_binding = dict(payload, _auren_engineering_readiness_digest="0" * 64)
        with self.assertRaisesRegex(ValueError, "task binding digest mismatch"):
            self.gate.verify_payload(wrong_binding)

    def test_gap_resolution_requires_evidence_and_dashboard_reads_latest_durable_state(self):
        blocked = self.assess({})
        gap = blocked.gap_ids[0]
        with self.assertRaisesRegex(ValueError, "requires evidence references"):
            self.gate.transition_gap(work_key="work:one", gap_id=gap, status="resolved")
        self.assess(READY_AXES)
        projection = EngineeringDashboard(self.root)._engineering_readiness()
        self.assertEqual("ready", projection["status"])
        self.assertEqual(0, projection["open_gap_count"])
        self.assertEqual(3, projection["resolved_gap_count"])

    def test_dashboard_fails_closed_when_the_canonical_journal_is_unreadable(self):
        self.assess({})
        with self.journal.path.open("a", encoding="utf-8") as stream:
            stream.write("{not-json}\n")
        projection = EngineeringDashboard(self.root)._engineering_readiness()
        self.assertEqual("unknown", projection["status"])
        self.assertIn("inspect journal integrity", projection["next_action"])

    def test_task_plan_does_not_schedule_engineering_tasks_before_readiness(self):
        task = Task("implement", "Implement change", tags=["engineering"])
        plan = TaskPlan([task])
        self.assertEqual([], plan.ready())
        blocked = self.assess({})
        plan.bind_engineering_readiness("implement", blocked)
        self.assertEqual([], plan.ready())
        ready = self.assess(READY_AXES)
        plan.bind_engineering_readiness("implement", ready)
        self.assertEqual(["implement"], [item.id for item in plan.ready()])
        self.assertTrue(task.metadata["engineering_readiness"]["envelope_digest"])

    def test_research_and_poc_timeboxes_survive_fresh_ledger_instances(self):
        now = [1_000.0]
        ledger = EngineeringBudgetLedger(self.journal, clock=lambda: now[0])
        research = ledger.begin("work:budget", "research")
        self.assertEqual(PHASE_LIMIT_SECONDS["research"], research.phase_deadline_epoch - now[0])
        self.assertEqual(BASIC_CYCLE_LIMIT_SECONDS, research.cycle_deadline_epoch - now[0])
        now[0] += 17
        resumed = EngineeringBudgetLedger(self.journal, clock=lambda: now[0]).begin("work:budget", "research")
        self.assertEqual(research.phase_deadline_epoch, resumed.phase_deadline_epoch)
        self.assertEqual(research.cycle_deadline_epoch, resumed.cycle_deadline_epoch)
        ledger.complete(resumed, outcome="passed", evidence_refs=("research-report:sha256",))
        with self.assertRaisesRegex(ExecutionBudgetExceeded, "already completed"):
            EngineeringBudgetLedger(self.journal, clock=lambda: now[0]).begin("work:budget", "research")

        poc = EngineeringBudgetLedger(self.journal, clock=lambda: now[0]).begin("work:budget", "poc")
        self.assertEqual(PHASE_LIMIT_SECONDS["poc"], poc.phase_deadline_epoch - now[0])
        self.assertEqual(research.cycle_deadline_epoch, poc.cycle_deadline_epoch)

    def test_cycle_deadline_expires_across_process_restart(self):
        now = [5_000.0]
        EngineeringBudgetLedger(self.journal, clock=lambda: now[0]).begin("work:expiry", "implementation")
        now[0] += BASIC_CYCLE_LIMIT_SECONDS + 1
        with self.assertRaisesRegex(ExecutionBudgetExceeded, "cycle timebox expired"):
            EngineeringBudgetLedger(self.journal, clock=lambda: now[0]).begin("work:expiry", "poc")
        self.assertTrue(any(row["event"] == "engineering_budget_exhausted" for row in self.journal._events()))


if __name__ == "__main__":
    unittest.main()
