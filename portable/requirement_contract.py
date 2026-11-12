"""Versioned requirement contracts and auditable conflict decisions."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

_REQUIREMENT_SCHEMA = "auren-requirement-contract-v1"
_PRIORITIES = {"must", "should", "could"}


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    statement: str
    acceptance_criteria: tuple[str, ...]
    priority: str = "must"
    source: str = "user"


@dataclass(frozen=True)
class RequirementConflict:
    """A declared material conflict that must be decided before execution."""

    conflict_id: str
    requirement_ids: tuple[str, ...]
    reason: str
    impact: str
    options: tuple[str, ...]
    recommended_option: str
    blocking: bool = True


@dataclass(frozen=True)
class RequirementDecision:
    """An immutable decision bound to one conflict and contract revision."""

    conflict_id: str
    selected_option: str
    rationale: str
    actor_id: str
    contract_revision: int
    evidence_ref: str = ""


@dataclass(frozen=True)
class RequirementContract:
    intent: str
    requirements: tuple[Requirement, ...]
    ambiguities: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    conflicts: tuple[RequirementConflict, ...] = ()
    decisions: tuple[RequirementDecision, ...] = ()
    revision: int = 1

    @property
    def unresolved_conflicts(self) -> tuple[RequirementConflict, ...]:
        decided = {decision.conflict_id for decision in self.decisions}
        return tuple(
            conflict for conflict in self.conflicts
            if conflict.blocking and conflict.conflict_id not in decided
        )

    @property
    def complete(self) -> bool:
        return (
            isinstance(self.intent, str)
            and bool(self.intent.strip())
            and bool(self.requirements)
            and all(requirement.acceptance_criteria for requirement in self.requirements)
            and not self.unresolved_conflicts
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _REQUIREMENT_SCHEMA,
            "intent": self.intent,
            "revision": self.revision,
            "requirements": [
                {
                    "requirement_id": item.requirement_id,
                    "statement": item.statement,
                    "acceptance_criteria": list(item.acceptance_criteria),
                    "priority": item.priority,
                    "source": item.source,
                }
                for item in self.requirements
            ],
            "ambiguities": list(self.ambiguities),
            "assumptions": list(self.assumptions),
            "conflicts": [
                {
                    "conflict_id": item.conflict_id,
                    "requirement_ids": list(item.requirement_ids),
                    "reason": item.reason,
                    "impact": item.impact,
                    "options": list(item.options),
                    "recommended_option": item.recommended_option,
                    "blocking": item.blocking,
                }
                for item in self.conflicts
            ],
            "decisions": [
                {
                    "conflict_id": item.conflict_id,
                    "selected_option": item.selected_option,
                    "rationale": item.rationale,
                    "actor_id": item.actor_id,
                    "contract_revision": item.contract_revision,
                    "evidence_ref": item.evidence_ref,
                }
                for item in self.decisions
            ],
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RequirementContract":
        if not isinstance(payload, Mapping) or payload.get("schema_version") != _REQUIREMENT_SCHEMA:
            raise ValueError("unsupported requirement contract schema")
        requirements = payload.get("requirements")
        conflicts = payload.get("conflicts", ())
        decisions = payload.get("decisions", ())
        ambiguities = payload.get("ambiguities", ())
        assumptions = payload.get("assumptions", ())
        collections = (requirements, conflicts, decisions, ambiguities, assumptions)
        if any(not isinstance(value, (list, tuple)) for value in collections):
            raise ValueError("requirement contract collections must be arrays")
        if any(not isinstance(item, Mapping) for item in (*requirements, *conflicts, *decisions)):
            raise ValueError("requirement contract records must be objects")
        for item in requirements:
            if not isinstance(item.get("acceptance_criteria"), (list, tuple)):
                raise ValueError("acceptance criteria must be an array")
        for item in conflicts:
            if not isinstance(item.get("requirement_ids"), (list, tuple)) or not isinstance(item.get("options"), (list, tuple)):
                raise ValueError("conflict requirement IDs and options must be arrays")
        engine = RequirementContractEngine()
        return engine.build(
            payload.get("intent", ""),
            tuple(Requirement(
                item["requirement_id"], item["statement"],
                tuple(item["acceptance_criteria"]),
                item.get("priority", "must"), item.get("source", "user"),
            ) for item in requirements),
            ambiguities=tuple(ambiguities),
            assumptions=tuple(assumptions),
            conflicts=tuple(RequirementConflict(
                item["conflict_id"], tuple(item["requirement_ids"]),
                item["reason"], item["impact"], tuple(item["options"]),
                item["recommended_option"], item.get("blocking", True),
            ) for item in conflicts),
            decisions=tuple(RequirementDecision(
                item["conflict_id"], item["selected_option"], item["rationale"],
                item["actor_id"], item["contract_revision"], item.get("evidence_ref", ""),
            ) for item in decisions),
            revision=payload.get("revision", 1),
        )


class RequirementContractEngine:
    """Build testable requirements and block execution on unresolved material conflicts."""

    def build(
        self,
        intent: str,
        requirements: Sequence[Requirement],
        *,
        ambiguities: Sequence[str] = (),
        assumptions: Sequence[str] = (),
        conflicts: Sequence[RequirementConflict] = (),
        decisions: Sequence[RequirementDecision] = (),
        revision: int = 1,
    ) -> RequirementContract:
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent is required")
        if type(revision) is not int or revision < 1:
            raise ValueError("contract revision must be a positive integer")
        items = tuple(requirements)
        if not items:
            raise ValueError("at least one requirement is required")
        if any(not isinstance(item, Requirement) for item in items):
            raise TypeError("requirements must contain Requirement records")
        ids = [item.requirement_id.strip() for item in items if isinstance(item.requirement_id, str)]
        if len(ids) != len(items) or any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("requirement IDs must be non-empty and unique")
        for item in items:
            if not isinstance(item.statement, str) or not item.statement.strip():
                raise ValueError("every requirement needs a statement and acceptance criteria")
            if not isinstance(item.acceptance_criteria, (list, tuple)) or not item.acceptance_criteria:
                raise ValueError("every requirement needs a statement and acceptance criteria")
            if any(not isinstance(value, str) or not value.strip() for value in item.acceptance_criteria):
                raise ValueError("acceptance criteria must be non-empty text")
            if not isinstance(item.priority, str) or item.priority not in _PRIORITIES:
                raise ValueError("invalid priority")
            if not isinstance(item.source, str) or not item.source.strip():
                raise ValueError("requirement source is required")

        clean_ambiguities = self._text_tuple(ambiguities, "ambiguities")
        clean_assumptions = self._text_tuple(assumptions, "assumptions")
        conflict_items = tuple(conflicts)
        decision_items = tuple(decisions)
        self._validate_conflicts(set(ids), conflict_items, decision_items, revision)
        return RequirementContract(
            intent.strip(), items, clean_ambiguities, clean_assumptions,
            conflict_items, decision_items, revision,
        )

    @staticmethod
    def _text_tuple(values: Sequence[str], field_name: str) -> tuple[str, ...]:
        if not isinstance(values, (list, tuple)):
            raise ValueError(f"{field_name} must be an array")
        result = tuple(values)
        if any(not isinstance(value, str) or not value.strip() for value in result):
            raise ValueError(f"{field_name} must contain non-empty text")
        return result

    @staticmethod
    def _validate_conflicts(
        requirement_ids: set[str],
        conflicts: tuple[RequirementConflict, ...],
        decisions: tuple[RequirementDecision, ...],
        revision: int,
    ) -> None:
        conflict_map: dict[str, RequirementConflict] = {}
        for conflict in conflicts:
            if not isinstance(conflict, RequirementConflict):
                raise TypeError("conflicts must contain RequirementConflict records")
            if not isinstance(conflict.conflict_id, str) or not conflict.conflict_id.strip() or conflict.conflict_id in conflict_map:
                raise ValueError("conflict IDs must be non-empty and unique")
            if not isinstance(conflict.requirement_ids, (list, tuple)):
                raise ValueError("conflict requirement IDs must be an array")
            linked = tuple(conflict.requirement_ids)
            if len(linked) < 2 or len(linked) != len(set(linked)) or any(item not in requirement_ids for item in linked):
                raise ValueError("conflicts must reference at least two distinct known requirements")
            if not isinstance(conflict.reason, str) or not conflict.reason.strip():
                raise ValueError("conflict reason is required")
            if not isinstance(conflict.impact, str) or not conflict.impact.strip():
                raise ValueError("conflict impact is required")
            if not isinstance(conflict.options, (list, tuple)):
                raise ValueError("conflict options must be an array")
            options = tuple(conflict.options)
            if len(options) < 2 or any(not isinstance(item, str) or not item.strip() for item in options) or len(options) != len(set(options)):
                raise ValueError("conflicts need at least two unique non-empty options")
            if conflict.recommended_option not in options:
                raise ValueError("recommended option must be one of the declared options")
            if type(conflict.blocking) is not bool:
                raise ValueError("conflict blocking flag must be boolean")
            conflict_map[conflict.conflict_id] = conflict

        decided: set[str] = set()
        for decision in decisions:
            if not isinstance(decision, RequirementDecision):
                raise TypeError("decisions must contain RequirementDecision records")
            if decision.conflict_id not in conflict_map:
                raise ValueError("decision references an unknown conflict")
            if decision.conflict_id in decided:
                raise ValueError("each conflict can have only one active decision")
            if decision.selected_option not in conflict_map[decision.conflict_id].options:
                raise ValueError("decision selects an undeclared conflict option")
            if not isinstance(decision.rationale, str) or not decision.rationale.strip():
                raise ValueError("decision rationale is required")
            if not isinstance(decision.actor_id, str) or not decision.actor_id.strip():
                raise ValueError("decision actor is required")
            if type(decision.contract_revision) is not int or not 1 <= decision.contract_revision <= revision:
                raise ValueError("decision revision must be bound to an existing contract revision")
            if not isinstance(decision.evidence_ref, str):
                raise ValueError("decision evidence reference must be text")
            if conflict_map[decision.conflict_id].blocking and not decision.evidence_ref.strip():
                raise ValueError("blocking conflict decisions require approval evidence")
            decided.add(decision.conflict_id)

    def decide(
        self,
        contract: RequirementContract,
        conflict_id: str,
        selected_option: str,
        *,
        rationale: str,
        actor_id: str,
        evidence_ref: str = "",
    ) -> RequirementContract:
        current = self.build(
            contract.intent, contract.requirements,
            ambiguities=contract.ambiguities, assumptions=contract.assumptions,
            conflicts=contract.conflicts, decisions=contract.decisions,
            revision=contract.revision,
        )
        conflict = next((item for item in current.conflicts if item.conflict_id == conflict_id), None)
        if conflict is None:
            raise KeyError("requirement conflict not found")
        if any(item.conflict_id == conflict_id for item in current.decisions):
            raise ValueError("conflict already has a decision; revise its requirements before deciding again")
        if selected_option not in conflict.options:
            raise ValueError("selected option is not declared for this conflict")
        if conflict.blocking and (not isinstance(evidence_ref, str) or not evidence_ref.strip()):
            raise ValueError("blocking conflict decisions require approval evidence")
        if not isinstance(rationale, str) or not rationale.strip() or not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("decision rationale and actor are required")
        next_revision = current.revision + 1
        decision = RequirementDecision(
            conflict_id, selected_option, rationale.strip(), actor_id.strip(),
            next_revision, evidence_ref,
        )
        return self.build(
            current.intent, current.requirements,
            ambiguities=current.ambiguities, assumptions=current.assumptions,
            conflicts=current.conflicts, decisions=current.decisions + (decision,),
            revision=next_revision,
        )

    def revise_requirement(
        self,
        contract: RequirementContract,
        requirement_id: str,
        replacement: Requirement,
    ) -> RequirementContract:
        current = self.build(
            contract.intent, contract.requirements,
            ambiguities=contract.ambiguities, assumptions=contract.assumptions,
            conflicts=contract.conflicts, decisions=contract.decisions,
            revision=contract.revision,
        )
        if not isinstance(replacement, Requirement) or replacement.requirement_id != requirement_id:
            raise ValueError("replacement must preserve the requirement ID")
        if not any(item.requirement_id == requirement_id for item in current.requirements):
            raise KeyError("requirement not found")
        revised = tuple(replacement if item.requirement_id == requirement_id else item for item in current.requirements)
        affected = {
            conflict.conflict_id for conflict in current.conflicts
            if requirement_id in conflict.requirement_ids
        }
        retained_decisions = tuple(
            decision for decision in current.decisions
            if decision.conflict_id not in affected
        )
        return self.build(
            current.intent, revised,
            ambiguities=current.ambiguities, assumptions=current.assumptions,
            conflicts=current.conflicts, decisions=retained_decisions,
            revision=current.revision + 1,
        )

    def validate(self, contract: RequirementContract) -> tuple[str, ...]:
        if not isinstance(contract, RequirementContract):
            return ("requirement contract has an invalid type",)
        issues = []
        if not contract.complete:
            issues.append("requirement contract is incomplete")
        for item in contract.requirements:
            if not item.acceptance_criteria:
                issues.append(f"{item.requirement_id}: missing acceptance criteria")
        if contract.ambiguities:
            issues.append("unresolved ambiguities remain")
        if contract.unresolved_conflicts:
            ids = ", ".join(item.conflict_id for item in contract.unresolved_conflicts)
            issues.append(f"blocking requirement conflicts remain: {ids}")
        return tuple(issues)


__all__ = [
    "Requirement", "RequirementConflict", "RequirementDecision",
    "RequirementContract", "RequirementContractEngine",
]
