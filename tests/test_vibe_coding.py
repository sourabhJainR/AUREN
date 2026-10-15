from __future__ import annotations

import unittest

from portable.orchestration import Orchestrator, RunStatus
from portable.vibe_coding import (
    STAGE_ORDER,
    STAGE_SPECS,
    VibeStage,
    VibeCodingCheckpoint,
    build_vibe_coding_graph,
)


def valid_output(stage: VibeStage) -> dict:
    out = {"status": "completed", "evidence": [f"verified {stage.value}"]}
    fields = {
        VibeStage.IDEA: {"problem": "User needs a simpler workflow", "success_metrics": ["acceptance criteria met"]},
        VibeStage.RESEARCH: {"market_evidence": ["reviewed alternatives"], "competitors": ["alternative-a"]},
        VibeStage.DEFINE_USER: {"personas": ["primary user"], "user_needs": ["complete task efficiently"]},
        VibeStage.PRD: {"requirements": ["core feature"], "acceptance_criteria": ["automated check passes"]},
        VibeStage.TECH_STACK: {"technology_decisions": ["Python: fits existing runtime"]},
        VibeStage.ARCHITECTURE: {"architecture_decisions": ["separated interface and domain logic"]},
        VibeStage.DESIGN: {"design_artifacts": ["wireframe-v1"]},
        VibeStage.PROJECT_RULES: {"rules": ["format", "test"]},
        VibeStage.TASK_BREAKDOWN: {"tasks": ["implement", "verify"]},
        VibeStage.SETUP: {"repository": "repo", "environment": {"python": "3.12"}},
        VibeStage.DEVELOPMENT: {"artifacts": ["src/feature.py"]},
        VibeStage.TESTING: {"tests_run": ["unit suite"], "status": "passed"},
        VibeStage.SECURITY_REVIEW: {"findings": [], "status": "passed", "critical_findings": 0},
        VibeStage.CODE_REVIEW: {"reviewer": "reviewer-b", "author": "author-a", "review_summary": "No blocking issues", "status": "passed", "blocking_findings": 0},
        VibeStage.PREVIEW_DEPLOYMENT: {"deployment_receipt": "preview-123"},
        VibeStage.QA_TESTING: {"qa_results": ["acceptance scenarios passed"], "user_feedback": ["user confirmed expected flow"], "status": "passed"},
        VibeStage.PRODUCTION_DEPLOY: {"release_receipt": "release-123", "deployed_revision": "abc123", "reviewed_revision": "abc123", "approval": {"approved": True, "approved_by": "release-manager", "approved_at": "2026-10-15T12:00:00Z"}},
        VibeStage.MONITORING: {"signals": ["error rate"], "alerts": ["error-rate alert"], "rollback_thresholds": ["error rate above SLO"]},
    }
    out.update(fields[stage])
    return out


class VibeCodingLifecycleTests(unittest.TestCase):
    def handlers(self, counts=None, fail_stage=None):
        counts = counts if counts is not None else {}
        handlers = {}
        for stage in STAGE_ORDER:
            def handle(state, _stage=stage):
                counts[_stage.value] = counts.get(_stage.value, 0) + 1
                if _stage == fail_stage:
                    raise RuntimeError("simulated restart boundary")
                return valid_output(_stage)
            handlers[stage] = handle
        return handlers

    def test_exactly_18_stages_in_user_requested_order(self):
        self.assertEqual(len(STAGE_ORDER), 18)
        self.assertEqual([spec.stage for spec in STAGE_SPECS], list(STAGE_ORDER))
        self.assertEqual(STAGE_ORDER[0], VibeStage.IDEA)
        self.assertEqual(STAGE_ORDER[-1], VibeStage.MONITORING)

    def test_every_stage_requires_an_explicit_handler(self):
        handlers = self.handlers()
        handlers.pop(VibeStage.RESEARCH)
        with self.assertRaisesRegex(ValueError, "all 18"):
            build_vibe_coding_graph(handlers)

    def test_full_workflow_runs_in_order_and_persists_each_stage(self):
        counts = {}
        saved = []
        graph = build_vibe_coding_graph(self.handlers(counts), checkpoint_writer=lambda value: saved.append(value))
        context = {
            "production_approval": {"approved": True, "approved_by": "release-manager", "approved_at": "2026-10-15T12:00:00Z"},
            "vibe_coding_workflow_id": "workflow-18",
            "final_code_revision": "abc123",
            "verified_code_revision": "abc123",
        }
        run = Orchestrator(graph, max_total_attempts=32).run("workflow-18", "Build the product", context)
        self.assertEqual(run.status, RunStatus.ACCEPTED, run.stop_reason)
        self.assertEqual(list(run.results), [stage.value for stage in STAGE_ORDER])
        self.assertEqual(len(saved), 18)
        self.assertEqual(saved[-1]["completed"], [stage.value for stage in STAGE_ORDER])
        self.assertTrue(all(counts.get(stage.value) == 1 for stage in STAGE_ORDER))
        resumed = VibeCodingCheckpoint.resume(saved[-1])
        self.assertEqual(resumed.workflow_id, "workflow-18")
        self.assertEqual(len(resumed.completed), 18)

    def test_restart_resume_does_not_replay_completed_stages(self):
        counts = {}
        saved = []
        first_graph = build_vibe_coding_graph(
            self.handlers(counts, fail_stage=VibeStage.SETUP),
            checkpoint_writer=lambda value: saved.append(value),
        )
        context = {"vibe_coding_workflow_id": "resume-1"}
        first = Orchestrator(first_graph, max_total_attempts=32).run("resume-1", "Build app", context)
        self.assertEqual(first.status, RunStatus.FAILED)
        self.assertEqual(saved[-1]["completed"], [stage.value for stage in STAGE_ORDER[:9]])
        self.assertEqual(counts.get(VibeStage.IDEA.value), 1)

        second_handlers = self.handlers(counts)
        second_graph = build_vibe_coding_graph(second_handlers, checkpoint_writer=lambda value: saved.append(value))
        resumed_context = {
            "vibe_coding_workflow_id": "resume-1",
            "vibe_coding_checkpoint": saved[-1],
            "production_approval": {"approved": True, "approved_by": "release-manager", "approved_at": "2026-10-15T12:00:00Z"},
            "final_code_revision": "abc123",
            "verified_code_revision": "abc123",
        }
        second = Orchestrator(second_graph, max_total_attempts=32).run("resume-1", "Build app", resumed_context)
        self.assertEqual(second.status, RunStatus.ACCEPTED, second.stop_reason)
        for stage in STAGE_ORDER[:9]:
            self.assertEqual(counts[stage.value], 1, f"{stage.value} should not replay")
        self.assertEqual(counts[VibeStage.SETUP.value], 2)

    def test_security_gate_failure_blocks_all_later_stages(self):
        counts = {}
        handlers = self.handlers(counts)
        def insecure(_state):
            counts[VibeStage.SECURITY_REVIEW.value] = 1
            result = valid_output(VibeStage.SECURITY_REVIEW)
            result["status"] = "failed"
            result["critical_findings"] = 1
            return result
        handlers[VibeStage.SECURITY_REVIEW] = insecure
        run = Orchestrator(build_vibe_coding_graph(handlers), max_total_attempts=32).run(
            "security-block", "Build safely"
        )
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertNotIn(VibeStage.CODE_REVIEW.value, counts)
        self.assertEqual(run.results[VibeStage.CODE_REVIEW.value].status.value, "skipped")

    def test_production_handler_never_runs_without_explicit_approval(self):
        counts = {}
        handlers = self.handlers(counts)
        run = Orchestrator(build_vibe_coding_graph(handlers), max_total_attempts=32).run(
            "no-prod-approval", "Build product"
        )
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertNotIn(VibeStage.PRODUCTION_DEPLOY.value, counts)
        self.assertNotIn(VibeStage.MONITORING.value, counts)

    def test_explicit_not_applicable_deployment_requires_rationale(self):
        from portable.vibe_coding import validate_stage_output
        ok, _ = validate_stage_output(VibeStage.PREVIEW_DEPLOYMENT, {
            "status": "not_applicable", "rationale": "Local CLI has no preview endpoint",
            "evidence": ["scope decision recorded"],
        })
        self.assertTrue(ok)
        ok, reason = validate_stage_output(VibeStage.PREVIEW_DEPLOYMENT, {
            "status": "not_applicable", "rationale": "   ", "evidence": ["decision"],
        })
        self.assertFalse(ok)
        self.assertIn("rationale", reason)

    def test_checkpoint_tampering_and_out_of_order_completion_fail_closed(self):
        snapshot = {
            "schema_version": "auren-vibe-coding-checkpoint-v1",
            "workflow_id": "bad",
            "completed": [VibeStage.RESEARCH.value],
            "outputs": {VibeStage.RESEARCH.value: valid_output(VibeStage.RESEARCH)},
            "blocked_stage": None,
        }
        import hashlib, json
        snapshot["digest"] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, "ordered prefix"):
            VibeCodingCheckpoint.resume(snapshot)
        snapshot["completed"] = []
        snapshot["outputs"] = {}
        snapshot["digest"] = "tampered"
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            VibeCodingCheckpoint.resume(snapshot)


if __name__ == "__main__":
    unittest.main()
