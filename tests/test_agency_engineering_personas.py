from pathlib import Path
import json
import unittest
import tempfile

from portable.agency_observability import LocalExporter, Tracer
from portable.agency_runtime import EvidenceItem, TaskProfile, execute
from portable.agency_regression_loop import RegressionPlan
from portable.agency_observability import Dataset, DatasetItem
from portable.engineering_personas import (
    AgentPersona,
    EngineeringPlan,
    PersonaReview,
    PersonaRoster,
    PersonaWorkflow,
)


def roster():
    return PersonaRoster(
        planner=AgentPersona("planner", "planner-1", "systems-planner"),
        plan_reviewer=AgentPersona("plan_reviewer", "plan-reviewer-1", "adversarial-plan-auditor"),
        implementer=AgentPersona("implementer", "implementer-1", "focused-implementation-engineer"),
        implementation_reviewer=AgentPersona("implementation_reviewer", "implementation-reviewer-1", "independent-security-and-quality-reviewer"),
    )


def plan(task, assignments):
    return EngineeringPlan(
        goal=task.request,
        scope=("change requested behavior", "preserve existing public contract"),
        assumptions=("existing APIs remain compatible",),
        acceptance_criteria=("targeted tests pass", "regression suite passes"),
        dependencies=("existing runtime contracts",),
        alternatives_considered=("minimal compatible change", "broader refactor rejected as unnecessary"),
        risks_and_mitigations=("regression risk; run focused and broad tests",),
        test_strategy=("unit tests", "integration regression"),
        rollback_plan=("revert the isolated commit", "verify previous revision and state"),
        budget={"max_attempts": 3, "max_minutes": 20},
        source_revision="test-revision",
        decision_rationale="Choose the smallest compatible change that meets acceptance criteria and retains a tested rollback path.",
        hypotheses=("The current runtime contract can support this change without a schema break.",),
        research_evidence_refs=("repo:runtime-contract",),
        unresolved_questions=(),
    )


def approved_plan_review(task, reviewed_plan):
    return PersonaReview(
        reviewer_id="plan-reviewer-1",
        target_id="planner-1",
        approved=True,
        evidence_refs=("plan-review:test-evidence",),
        dimensions={"completeness": 1.0, "feasibility": 0.9},
    )


def approved_implementation_review(task, reviewed_plan, artifact):
    assert artifact["deliverables"]
    assert reviewed_plan.plan_digest
    return PersonaReview(
        reviewer_id="implementation-reviewer-1",
        target_id="implementer-1",
        approved=True,
        evidence_refs=("implementation-review:test-evidence", "tests:test-run"),
        dimensions={"correctness": 1.0, "security": 1.0, "regression": 1.0},
    )


class TestEngineeringPersonas(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temporary_directory.name)

    def tearDown(self):
        self._temporary_directory.cleanup()

    def test_persona_roster_rejects_shared_planner_implementer_persona(self):
        roles = roster()
        invalid = PersonaRoster(
            planner=roles.planner,
            plan_reviewer=roles.plan_reviewer,
            implementer=AgentPersona("implementer", "implementer-1", "systems-planner"),
            implementation_reviewer=roles.implementation_reviewer,
        )
        with self.assertRaisesRegex(ValueError, "different personas"):
            invalid.validate()



    def test_persona_workflow_reviews_plan_before_implementation_and_reviews_result(self):
        calls = []
        workflow = PersonaWorkflow(
            roster=roster(),
            plan=lambda task, assignments: (calls.append("plan") or plan(task, assignments)),
            review_plan=lambda task, reviewed: (calls.append("plan_review") or approved_plan_review(task, reviewed)),
            review_implementation=lambda task, reviewed, artifact: (
                calls.append("implementation_review") or approved_implementation_review(task, reviewed, artifact)
            ),
        )
        tracer = Tracer(LocalExporter(self.tmp_path / "traces.jsonl", enabled=True))
        task = TaskProfile("persona-task", "implement safe behavior", risk="normal")

        def worker(task, assignments):
            calls.append("implement")
            assert task.approved_plan is not None
            assert task.approved_plan["plan_digest"]
            return {
                "deliverables": ["patch-ref"],
                "verification": ["targeted tests passed"],
                "dimensions": {
                    "correctness": 25, "completeness": 15, "evidence": 15, "verification": 15,
                    "scope_discipline": 10, "security_and_safety": 10, "clarity": 5, "maintainability": 5,
                },
                "hard_gates": {"tests": True},
                "evidence": [EvidenceItem("test-run", "targeted tests passed", "tests:test-run")],
                "provider_route": {"provider": "test-provider", "model": "test-model", "reason": "fixture route"},
                "usage": {"input_tokens": 12, "output_tokens": 8, "cost_usd": 0.01, "latency_ms": 4.2},
                "observability_events": [{
                    "name": "unit-test-tool",
                    "phase": "tool",
                    "status": "ok",
                    "input": "PRIVATE PROMPT MUST NOT BE COPIED",
                    "input_digest": "sha256:fixture",
                    "output_ref": "tests:unit-test",
                    "duration_ms": 4.2,
                    "evidence_refs": ["tests:unit-test"],
                }],
            }

        result = execute(task, worker, tracer=tracer, persona_workflow=workflow)
        assert calls == ["plan", "plan_review", "implement", "implementation_review"]
        assert result.receipt is not None and result.receipt.passed
        assert result.hard_gates["independent_implementation_review"] is True
        trace_text = (self.tmp_path / "traces.jsonl").read_text()
        assert "independent_plan_review" in trace_text
        assert "independent_implementation_review" in trace_text
        assert "plan_digest" in trace_text
        assert '"phase":"ideation"' in trace_text
        assert '"phase":"research"' in trace_text
        assert '"phase":"verification"' in trace_text
        assert '"phase":"tool"' in trace_text
        assert '"provider":"test-provider"' in trace_text
        assert "PRIVATE PROMPT MUST NOT BE COPIED" not in trace_text



    def test_plan_rejection_blocks_implementation(self):
        calls = []
        rejected = PersonaReview(
            reviewer_id="plan-reviewer-1",
            target_id="planner-1",
            approved=False,
            findings=("missing threat model",),
            evidence_refs=("plan-review:rejection",),
        )
        workflow = PersonaWorkflow(
            roster=roster(),
            plan=lambda task, assignments: (calls.append("plan") or plan(task, assignments)),
            review_plan=lambda task, reviewed: (calls.append("plan_review") or rejected),
            review_implementation=approved_implementation_review,
        )
        task = TaskProfile("blocked-plan", "high risk change", risk="high")

        def worker(task, assignments):
            calls.append("implement")
            raise AssertionError("implementation must not run after plan rejection")

        with self.assertRaisesRegex(ValueError, "missing threat model"):
            execute(task, worker, persona_workflow=workflow)
        assert calls == ["plan", "plan_review"]



    def test_high_risk_and_release_execution_require_independent_personas(self):
        task = TaskProfile("high-risk", "change authority boundary", risk="critical")
        with self.assertRaisesRegex(ValueError, "require independent persona review"):
            execute(task, lambda task, assignments: {})




    def test_legacy_trace_cannot_authorize_promotion_without_independent_review(self):
        task = TaskProfile("legacy-promotion", "safe change")
        regression = RegressionPlan(
            dataset=Dataset("pinned", "1", (DatasetItem("case", "ok"),)),
            execute_case=lambda trace, item: "ok",
            judge=lambda trace, item, output: {"correctness": float(output == item.expected)},
        )

        def worker(task, assignments):
            return {
                "deliverables": ["patch-ref"],
                "verification": ["unit tests passed"],
                "dimensions": {
                    "correctness": 25, "completeness": 15, "evidence": 15, "verification": 15,
                    "scope_discipline": 10, "security_and_safety": 10, "clarity": 5, "maintainability": 5,
                },
                "hard_gates": {"tests": True},
                "evidence": [EvidenceItem("test-run", "unit tests passed", "tests:run")],
            }

        result = execute(task, worker, regression_plan=regression)
        assert result.regression_run is not None and result.regression_run.passed
        assert result.promotion_decision is not None
        assert result.promotion_decision.action == "rollback"
        assert result.promotion_decision.reason == "hard gate failed"




    def test_implementation_review_rejection_is_blocked_and_preserves_findings(self):
        rejected = PersonaReview(
            reviewer_id="implementation-reviewer-1",
            target_id="implementer-1",
            approved=False,
            findings=("authorization bypass remains",),
            evidence_refs=("review:security-finding",),
            dimensions={"security": 0.2},
        )
        workflow = PersonaWorkflow(
            roster=roster(),
            plan=plan,
            review_plan=approved_plan_review,
            review_implementation=lambda task, reviewed, artifact: rejected,
        )
        trace_path = self.tmp_path / "trace.jsonl"
        tracer = Tracer(LocalExporter(trace_path, enabled=True))
        task = TaskProfile("review-block", "secure behavior", source_revision="test-revision")

        def worker(task, assignments):
            return {
                "deliverables": ["patch-ref"],
                "verification": ["unit tests passed"],
                "dimensions": {
                    "correctness": 25, "completeness": 15, "evidence": 15, "verification": 15,
                    "scope_discipline": 10, "security_and_safety": 10, "clarity": 5, "maintainability": 5,
                },
                "hard_gates": {"tests": True},
                "evidence": [EvidenceItem("test-run", "unit tests passed", "tests:run")],
            }

        with self.assertRaisesRegex(ValueError, "authorization bypass remains"):
            execute(task, worker, tracer=tracer, persona_workflow=workflow)
        latest = json.loads(trace_path.read_text().splitlines()[-1])
        assert latest["status"] == "blocked"
        assert latest["metadata"]["implementation_review"]["findings"] == ["authorization bypass remains"]
        assert "corrective task" in latest["metadata"]["next_action"]
