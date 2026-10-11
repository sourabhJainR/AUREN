"""Regression tests for fail-closed decision and promotion gates."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from portable.decision_gate import validate_decision_record

REPO = "owner/project"
BASE = "a" * 40
HEAD = "b" * 40
APPROVER = "release-manager"


def valid_record() -> dict:
    return {
        "schema_version": "1.0",
        "task_id": "task-17",
        "repository": REPO,
        "base_commit": BASE,
        "head_commit": HEAD,
        "risk_level": "medium",
        "acceptance_criteria": ["Regression test passes"],
        "selected_option": "Small isolated patch",
        "selection_rationale": "Meets the contract with the smallest blast radius",
        "alternatives": [{"option": "Broad refactor", "reason_rejected": "Not required to meet acceptance criteria"}],
        "dependencies": [],
        "checks": [{
            "name": "focused-regression",
            "command": "python -m unittest tests.test_decision_gate",
            "status": "passed",
            "commit_sha": HEAD,
            "evidence_id": "run-123",
            "evaluator_type": "internal",
        }],
        "limitations": ["No independent evaluator was used"],
        "rollback_trigger": "Any required regression check fails after merge",
        "rollback_action": "Revert the isolated commit and rerun the required checks",
        "retry_history": [],
        "promotion": {"level": "none"},
    }


class DecisionGateTests(unittest.TestCase):
    def errors(self, record: dict, **kwargs) -> list[str]:
        return validate_decision_record(record, expected_repository=REPO, expected_head=HEAD, **kwargs)

    def test_json_schema_is_valid_and_requires_core_evidence(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "task-decision-record-v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")
        self.assertTrue({"base_commit", "head_commit", "acceptance_criteria", "checks", "rollback_action", "promotion"}.issubset(schema["required"]))
        self.assertTrue(any("last_known_good_commit" in rule.get("then", {}).get("required", []) for rule in schema.get("allOf", [])))

    def test_accepts_well_formed_unpromoted_record(self) -> None:
        self.assertEqual(self.errors(valid_record()), [])

    def test_rejects_missing_acceptance_criteria_and_rollback(self) -> None:
        record = valid_record()
        record["acceptance_criteria"] = []
        record["rollback_action"] = ""
        errors = self.errors(record)
        self.assertTrue(any("acceptance_criteria" in e for e in errors))
        self.assertTrue(any("rollback_action" in e for e in errors))

    def test_rejects_stale_head_and_check_evidence(self) -> None:
        record = valid_record()
        record["head_commit"] = "c" * 40
        record["checks"][0]["commit_sha"] = "c" * 40
        errors = self.errors(record)
        self.assertTrue(any("stale evidence" in e for e in errors))
        self.assertTrue(any("expected head" in e for e in errors))

    def test_promotion_requires_authorized_approval_and_passed_checks(self) -> None:
        record = valid_record()
        record["promotion"] = {"level": "verified_for_scope", "approved_by": "someone", "approval_evidence_id": "approval-1"}
        self.assertTrue(any("authorized approver" in e for e in self.errors(record)))
        record["promotion"]["approved_by"] = APPROVER
        record["checks"][0]["status"] = "failed"
        self.assertTrue(any("every recorded check passed" in e for e in self.errors(record, authorized_approvers={APPROVER}, known_evidence_ids={"run-123", "approval-1"})))

    def test_independent_promotion_requires_external_lineage(self) -> None:
        record = valid_record()
        record["promotion"] = {"level": "independently_corroborated", "approved_by": APPROVER, "approval_evidence_id": "approval-1"}
        errors = self.errors(record, authorized_approvers={APPROVER})
        self.assertTrue(any("external_evaluator_id" in e for e in errors))
        self.assertTrue(any("separately controlled evaluator" in e for e in errors))

    def test_promotion_requires_canonical_evidence_references(self) -> None:
        record = valid_record()
        record["promotion"] = {"level": "verified_for_scope", "approved_by": APPROVER, "approval_evidence_id": "approval-1"}
        self.assertTrue(any("known_evidence_ids" in e for e in self.errors(record, authorized_approvers={APPROVER})))
        errors = self.errors(record, authorized_approvers={APPROVER}, known_evidence_ids={"approval-1"})
        self.assertTrue(any("unknown canonical evidence_id" in e for e in errors))

    def test_independent_promotion_requires_an_independent_check(self) -> None:
        record = valid_record()
        record["promotion"] = {
            "level": "independently_corroborated",
            "approved_by": APPROVER,
            "approval_evidence_id": "approval-1",
            "external_evaluator_id": "eval-1",
            "corpus_owner_id": "corpus-owner-1",
            "receipt_digest": "sha256:receipt",
            "repeat_run_id": "repeat-1",
            "evaluator_control": "external",
            "corpus_control": "external",
        }
        errors = self.errors(record, authorized_approvers={APPROVER}, known_evidence_ids={"run-123", "approval-1"})
        self.assertTrue(any("passing independent/external evaluator check" in e for e in errors))

    def test_rejects_claimed_independence_with_untrusted_control_values(self) -> None:
        record = valid_record()
        record["promotion"] = {
            "level": "independently_corroborated",
            "approved_by": APPROVER,
            "approval_evidence_id": "approval-1",
            "external_evaluator_id": "eval-1",
            "corpus_owner_id": "corpus-owner-1",
            "receipt_digest": "sha256:receipt",
            "repeat_run_id": "repeat-1",
            "evaluator_control": "bogus",
            "corpus_control": "bogus",
        }
        errors = self.errors(record, authorized_approvers={APPROVER})
        self.assertTrue(any("separately controlled evaluator" in e for e in errors))
        self.assertTrue(any("separately controlled corpus/oracle" in e for e in errors))

    def test_rejects_repeated_no_progress_retry(self) -> None:
        record = valid_record()
        record["retry_history"] = [
            {"hypothesis": "timeout", "strategy": "retry same command", "new_evidence_ids": []},
            {"hypothesis": "timeout", "strategy": "retry same command", "new_evidence_ids": []},
        ]
        self.assertTrue(any("no-progress retry" in e for e in self.errors(record)))

    def test_retry_is_allowed_when_new_evidence_changes_the_decision(self) -> None:
        record = valid_record()
        record["retry_history"] = [
            {"hypothesis": "timeout", "strategy": "retry same command", "new_evidence_ids": ["log-1"]},
            {"hypothesis": "timeout", "strategy": "retry same command", "new_evidence_ids": ["log-2"]},
        ]
        self.assertEqual(self.errors(record), [])

    def test_requires_option_alternative_for_medium_risk(self) -> None:
        record = valid_record()
        record["alternatives"] = []
        self.assertTrue(any("at least one alternative" in e for e in self.errors(record)))


    def test_high_and_critical_risk_require_distinct_last_known_good_sha(self) -> None:
        for risk in ("high", "critical"):
            record = valid_record()
            record["risk_level"] = risk
            errors = self.errors(record)
            self.assertTrue(any("last_known_good_commit" in error for error in errors))
            record["last_known_good_commit"] = BASE
            self.assertEqual(self.errors(record), [])
            record["last_known_good_commit"] = HEAD
            self.assertTrue(any("must differ from the candidate head" in error for error in self.errors(record)))

    def test_low_and_medium_risk_allow_omitted_last_known_good_sha(self) -> None:
        record = valid_record()
        self.assertEqual(self.errors(record), [])
        record["last_known_good_commit"] = "not-a-commit"
        self.assertTrue(any("last_known_good_commit must be a full" in error for error in self.errors(record)))


if __name__ == "__main__":
    unittest.main()
