import unittest
import hashlib
import json
import tempfile
from pathlib import Path

from portable.evaluation_rubric import (
    RubricCriterion, RubricDefinition, evaluate_rubric, persist_rubric_receipt,
    engineering_delivery_rubric, rubric_plan_binding,
)


def rubric():
    return RubricDefinition(
        rubric_id="engineering-delivery",
        version="1.0.0",
        criteria=(
            RubricCriterion("correctness", "Meets the requested behavior", 40, ("tests",), True),
            RubricCriterion("evidence", "Claims are traceable", 30, ("evidence_report",)),
            RubricCriterion("maintainability", "Changes are maintainable", 30, ("review",)),
        ),
        hard_gate_ids=("guard", "required_ci"),
    )


def evaluate(**overrides):
    values = dict(
        subject_id="candidate-1", run_id="run-1", source_digest="a" * 64,
        plan_digest="c" * 64, artifact_digests=("b" * 64,),
        evaluator_id="independent-qa", evaluator_version="qa-v1",
        scores={"correctness": 100, "evidence": 100, "maintainability": 100},
        evidence={"correctness": ["tests://run-1"], "evidence": ["evidence_report://run-1"],
                  "maintainability": ["review://run-1"]},
        hard_gates={"guard": True, "required_ci": True},
        rationales={"correctness": "Acceptance tests pass", "evidence": "All claims are linked", "maintainability": "Review found no material issue"},
        uncertainty=0.05,
        executor_id="implementer",
    )
    values.update(overrides)
    return evaluate_rubric(rubric(), **values)


class EvaluationRubricTests(unittest.TestCase):
    def test_rubric_digest_is_stable_and_version_bound(self):
        first = rubric()
        self.assertEqual(first.digest, rubric().digest)
        changed = RubricDefinition(first.rubric_id, "1.0.1", first.criteria, hard_gate_ids=first.hard_gate_ids)
        self.assertNotEqual(first.digest, changed.digest)


    def test_plan_binding_pins_rubric_digest_and_task_ids(self):
        binding = rubric_plan_binding(
            rubric(), plan_id="plan-1", plan_digest="c" * 64,
            task_ids=("task-1", "task-2"),
        )
        self.assertEqual(binding["rubric_digest"], rubric().digest)
        self.assertEqual(binding["task_ids"], ("task-1", "task-2"))
        self.assertEqual(len(binding["binding_digest"]), 64)

    def test_missing_rationale_or_artifact_digest_keeps_receipt_unscored_or_rejected(self):
        receipt = evaluate(rationales={"correctness": "ok", "evidence": "ok"})
        self.assertEqual(receipt.status, "unscored")
        with self.assertRaises(ValueError):
            evaluate(artifact_digests=())

    def test_receipt_includes_plan_artifacts_rationales_and_uncertainty(self):
        receipt = evaluate()
        payload = receipt.as_dict()
        self.assertEqual(payload["plan_digest"], "c" * 64)
        self.assertEqual(payload["artifact_digests"], ["b" * 64])
        self.assertEqual(payload["uncertainty"], 0.05)
        self.assertIn("correctness", payload["criterion_rationales"])

    def test_standard_engineering_profile_has_independent_release_gates(self):
        standard = engineering_delivery_rubric()
        self.assertEqual(sum(item.weight for item in standard.criteria), 100)
        self.assertIn("guard", standard.hard_gate_ids)
        self.assertIn("rollback", standard.hard_gate_ids)

    def test_weights_must_sum_to_one_hundred(self):
        with self.assertRaises(ValueError):
            RubricDefinition("bad-rubric", "1", (RubricCriterion("a", "A", 50),))

    def test_missing_criterion_evidence_is_unscored_not_zero_or_pass(self):
        receipt = evaluate(evidence={"correctness": ["tests://run-1"], "evidence": [],
                                     "maintainability": ["review://run-1"]})
        self.assertEqual(receipt.status, "unscored")
        self.assertIsNone(receipt.weighted_score)

    def test_failed_hard_gate_cannot_be_offset_by_perfect_weighted_score(self):
        receipt = evaluate(hard_gates={"guard": False, "required_ci": True})
        self.assertEqual(receipt.status, "blocked")
        self.assertEqual(receipt.weighted_score, 100.0)

    def test_unknown_gate_is_unscored_and_missing_gate_never_passes(self):
        receipt = evaluate(hard_gates={"guard": True})
        self.assertEqual(receipt.status, "unscored")

    def test_executor_cannot_self_evaluate(self):
        with self.assertRaises(ValueError):
            evaluate(evaluator_id="implementer")

    def test_receipt_is_bound_to_exact_source_run_rubric_and_evaluator(self):
        receipt = evaluate()
        self.assertEqual(receipt.rubric_digest, rubric().digest)
        self.assertEqual(receipt.subject_id, "candidate-1")
        self.assertEqual(receipt.run_id, "run-1")
        self.assertEqual(len(receipt.receipt_digest), 64)
        self.assertEqual(receipt.status, "passed")


    def test_mandatory_criterion_is_a_hard_gate(self):
        receipt = evaluate(scores={"correctness": 99, "evidence": 100, "maintainability": 100})
        self.assertEqual(receipt.status, "blocked")
        self.assertGreaterEqual(receipt.weighted_score, 90.0)


    def test_receipt_persists_to_existing_canonical_evidence_ledger_idempotently(self):
        from portable.canonical_evidence_ledger import CanonicalEvidenceLedger
        receipt = evaluate()
        with tempfile.TemporaryDirectory() as directory:
            ledger = CanonicalEvidenceLedger(Path(directory) / "evidence.sqlite3")
            first = persist_rubric_receipt(
                ledger, receipt, repository="owner/repo",
                head_sha="a" * 40,
            )
            second = persist_rubric_receipt(
                ledger, receipt, repository="owner/repo",
                head_sha="a" * 40,
            )
            self.assertEqual(first, second)
            self.assertEqual(first["evidence_type"], "rubric_score")
            self.assertEqual(first["status"], "passed")


    def test_serialized_payload_reproduces_the_receipt_digest(self):
        receipt = evaluate()
        payload = receipt.as_dict()
        claimed = payload.pop("receipt_digest")
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        self.assertEqual(claimed, hashlib.sha256(canonical).hexdigest())
        self.assertEqual(receipt.payload(), payload)

    def test_wrong_evidence_kind_and_non_boolean_gate_cannot_pass(self):
        wrong_kind = evaluate(evidence={
            "correctness": ["attachment://not-tests"],
            "evidence": ["evidence://run-1"],
            "maintainability": ["review://run-1"],
        })
        self.assertEqual(wrong_kind.status, "unscored")
        with self.assertRaises(ValueError):
            evaluate(hard_gates={"guard": "true", "required_ci": True})

    def test_missing_required_evidence_kind_is_unscored(self):
        receipt = evaluate(evidence={
            "correctness": ["tests://run-1"],
            "evidence": ["attachment://report"],
            "maintainability": ["review://run-1"],
        })
        self.assertEqual(receipt.status, "unscored")


    def test_not_applicable_criterion_is_explicit_and_weights_are_renormalized(self):
        scoped = RubricDefinition(
            rubric_id="delivery-scope",
            version="1.0.0",
            criteria=(
                RubricCriterion("correctness", "Correctness", 70, ("tests",), True, ("all",)),
                RubricCriterion("operations", "Operational readiness", 30, ("ops",), False, ("deployment",)),
            ),
            hard_gate_ids=("guard",),
        )
        receipt = evaluate_rubric(
            scoped,
            subject_id="candidate", run_id="run-1", source_digest="a" * 64,
            plan_digest="c" * 64, artifact_digests=("b" * 64,),
            evaluator_id="qa", evaluator_version="qa-v1", executor_id="builder",
            scores={"correctness": 100},
            evidence={"correctness": ["tests://run-1"]},
            rationales={"correctness": "Tests pass"},
            applicability={"correctness": True, "operations": False},
            applicability_rationales={"operations": "No deployment is part of this change"},
            hard_gates={"guard": True},
        )
        self.assertEqual("passed", receipt.status)
        self.assertEqual(100.0, receipt.weighted_score)
        self.assertEqual({"correctness": True, "operations": False}, dict(receipt.criterion_applicability))
        self.assertIsNone(dict(receipt.criterion_scores)["operations"])
        self.assertEqual(
            "No deployment is part of this change",
            dict(receipt.criterion_applicability_rationales)["operations"],
        )

    def test_scoped_criterion_requires_explicit_applicability_decision(self):
        scoped = RubricDefinition(
            rubric_id="scope-check", version="1.0.0",
            criteria=(
                RubricCriterion("tests", "Tests", 100, ("tests",), False, ("repository",)),
            ),
        )
        with self.assertRaisesRegex(ValueError, "explicit applicability"):
            evaluate_rubric(
                scoped, subject_id="candidate", run_id="run-1",
                source_digest="a" * 64, plan_digest="c" * 64,
                artifact_digests=("b" * 64,), evaluator_id="qa",
                evaluator_version="qa-v1", executor_id="builder",
                scores={"tests": 100}, evidence={"tests": ["tests://run-1"]},
                rationales={"tests": "Passed"}, hard_gates={},
            )

    def test_mandatory_criterion_cannot_be_waived_as_not_applicable(self):
        with self.assertRaisesRegex(ValueError, "mandatory criterion"):
            evaluate(
                applicability={"correctness": False},
                applicability_rationales={"correctness": "Claimed not relevant"},
                scores={"evidence": 100, "maintainability": 100},
                evidence={"evidence": ["evidence_report://run-1"], "maintainability": ["review://run-1"]},
                rationales={"evidence": "Linked", "maintainability": "Reviewed"},
            )

    def test_unknown_criterion_and_invalid_score_are_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(scores={"unknown": 100})
        with self.assertRaises(ValueError):
            evaluate(scores={"correctness": 101, "evidence": 100, "maintainability": 100})


if __name__ == "__main__":
    unittest.main()
