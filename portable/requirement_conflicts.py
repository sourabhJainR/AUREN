"""Structured requirement conflict detection for evidence-gated engineering plans.

This module does not infer facts from free text. A trusted requirements parser
maps user statements to typed facts; the analyzer detects known incompatibilities
deterministically. It never chooses an option or authorizes blocked work.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from collections.abc import Mapping, Sequence

@dataclass(frozen=True, slots=True)
class RequirementFact:
    statement_id: str
    key: str
    value: str
    statement: str
    scope: str = "global"
    def __post_init__(self) -> None:
        for name in ("statement_id", "key", "value", "statement", "scope"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or not value.isprintable():
                raise ValueError(f"{name} must be non-empty printable text")
        if max(map(len, (self.statement_id, self.key, self.value, self.scope))) > 128:
            raise ValueError("requirement fact identity fields exceed their length limit")
        if len(self.statement) > 2000:
            raise ValueError("requirement statement exceeds the 2000-character limit")

@dataclass(frozen=True, slots=True)
class ConflictOption:
    option_id: str
    label: str
    trade_off: str

@dataclass(frozen=True, slots=True)
class RequirementConflict:
    conflict_id: str
    key: str
    scope: str
    title: str
    statements: tuple[RequirementFact, ...]
    impact: str
    options: tuple[ConflictOption, ...]
    recommended_option: str
    decision_required: bool = True

@dataclass(frozen=True, slots=True)
class RequirementConflictReport:
    conflicts: tuple[RequirementConflict, ...]
    blocked_workstreams: tuple[str, ...]
    independent_workstreams: tuple[str, ...]
    fact_count: int
    unrecognized_keys: tuple[str, ...] = ()
    @property
    def status(self) -> str:
        if self.fact_count == 0:
            return "unscored"
        if self.unrecognized_keys:
            return "partial"
        return "decision_required" if self.conflicts else "clear"

@dataclass(frozen=True, slots=True)
class RequirementDecision:
    decision_id: str
    conflict_id: str
    selected_option: str
    decision_maker: str
    rationale: str
    prior_plan_digest: str
    new_plan_revision: int
    digest: str

def _o(option_id: str, label: str, trade_off: str) -> ConflictOption:
    return ConflictOption(option_id, label, trade_off)

@dataclass(frozen=True, slots=True)
class _Rule:
    key: str
    pairs: frozenset[frozenset[str]]
    title: str
    impact: str
    options: tuple[ConflictOption, ...]
    recommended_option: str

_RULES = (
    _Rule("market_data_latency", frozenset({frozenset({"real_time", "delayed"})}),
        "Real-time quotes conflict with delayed-only market data",
        "Quote freshness and recommendation timing cannot meet both requirements.",
        (_o("accept_delayed", "Accept delayed quotes with clear timestamps", "Lower cost; recommendations are not real-time."),
         _o("use_realtime_provider", "Use a licensed real-time provider", "Higher cost and licensing work; supports freshness."),
         _o("remove_live_quote_scope", "Remove live quotes from the MVP", "Reduces MVP scope.")), "accept_delayed"),
    _Rule("data_redistribution", frozenset({frozenset({"public_redistribution", "prohibited"})}),
        "Public redistribution conflicts with provider restrictions",
        "Publishing restricted market data may breach provider terms or law.",
        (_o("license_redistribution", "Select a provider license permitting redistribution", "Adds cost and legal review."),
         _o("private_display", "Restrict data to permitted private display", "Limits sharing and public access."),
         _o("use_permissive_source", "Use a source with compatible redistribution rights", "May reduce coverage or freshness.")), "use_permissive_source"),
    _Rule("user_state", frozenset({frozenset({"no_accounts", "per_user_saved_state"})}),
        "No user accounts conflicts with per-user saved portfolios",
        "Per-user state needs identity or an alternative local ownership model.",
        (_o("local_only", "Keep watchlists local to the device", "No cross-device sync or account recovery."),
         _o("add_accounts", "Add authenticated user accounts", "Adds identity, privacy and security scope."),
         _o("shared_state", "Use a shared non-personal workspace", "Saved data is not private to an individual.")), "local_only"),
    _Rule("recommendation_approval", frozenset({frozenset({"fully_automated", "human_approval_required"})}),
        "Fully automated recommendations conflict with mandatory human approval",
        "The system cannot publish without review and require approval before publication.",
        (_o("human_gate", "Require human approval before publication", "Adds review time and preserves the approval control."),
         _o("automate_internal", "Automate analysis but keep publication approval", "Automation is limited to internal preparation."),
         _o("remove_approval", "Remove the approval requirement", "Requires an explicit policy and risk decision.")), "human_gate"),
    _Rule("data_retention", frozenset({frozenset({"no_external_storage", "historical_analytics"})}),
        "No external storage conflicts with durable historical analytics",
        "Historical analytics needs retention beyond transient processing.",
        (_o("local_history", "Store history locally on the user's device", "Limited backup and multi-device access."),
         _o("approved_storage", "Use an approved external store", "Requires privacy, retention and security controls."),
         _o("no_history", "Remove historical analytics from scope", "Only current or transient analysis is available.")), "local_history"),
    _Rule("delivery_scope", frozenset({frozenset({"fast_mvp", "broad_coverage_ha_compliance"})}),
        "Fast MVP timing conflicts with broad coverage, high availability and extensive compliance",
        "The combined scope is unlikely to fit the requested delivery window without reducing quality or increasing cost.",
        (_o("phased_delivery", "Deliver a narrow MVP, then add coverage and controls in phases", "Some capabilities arrive later."),
         _o("extend_timeline", "Extend the delivery timeline", "Delays the first release."),
         _o("increase_resources", "Increase approved delivery resources", "Raises cost and still requires scope control.")), "phased_delivery"),
)

class RequirementConflictAnalyzer:
    """Detect structured incompatibilities and produce explicit decision requests."""
    @classmethod
    def analyze(cls, facts: Sequence[RequirementFact], *,
                workstream_dependencies: Mapping[str, Sequence[str]] | None = None) -> RequirementConflictReport:
        if isinstance(facts, (str, bytes)) or not isinstance(facts, Sequence):
            raise ValueError("facts must be a sequence of RequirementFact values")
        if any(not isinstance(item, RequirementFact) for item in facts):
            raise ValueError("every requirement fact must be a RequirementFact")
        ids = [item.statement_id for item in facts]
        if len(ids) != len(set(ids)):
            raise ValueError("requirement statement IDs must be unique")
        by_key: dict[str, list[RequirementFact]] = {}
        for fact in facts:
            by_key.setdefault(fact.key, []).append(fact)
        conflicts = []
        blocked_keys = set()
        for rule in _RULES:
            items = by_key.get(rule.key, [])
            for scope in sorted({item.scope for item in items}):
                scoped_items = [item for item in items if item.scope == scope]
                values = {item.value for item in scoped_items}
                found = [
                    item for item in scoped_items
                    if any(pair.issubset(values) and item.value in pair for pair in rule.pairs)
                ]
                if not found:
                    continue
                ordered = tuple(sorted(found, key=lambda item: (item.statement_id, item.value)))
                identity = {"key": rule.key, "scope": scope, "statements": [
                    {"id": item.statement_id, "value": item.value, "statement": item.statement}
                    for item in ordered]}
                conflict_id = hashlib.sha256(json.dumps(
                    identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")).hexdigest()
                conflicts.append(RequirementConflict(
                    conflict_id, rule.key, scope, rule.title, ordered, rule.impact,
                    rule.options, rule.recommended_option))
                blocked_keys.add(rule.key)
        blocked, independent = [], []
        dependencies = workstream_dependencies or {}
        if not isinstance(dependencies, Mapping):
            raise ValueError("workstream_dependencies must be a mapping")
        for workstream, keys in dependencies.items():
            if not isinstance(workstream, str) or not workstream.strip():
                raise ValueError("workstream names must be non-empty text")
            if isinstance(keys, (str, bytes)) or not isinstance(keys, Sequence):
                raise ValueError("each workstream dependency must be a sequence")
            if any(not isinstance(key, str) or not key.strip() for key in keys):
                raise ValueError("workstream requirement keys must be non-empty text")
            (blocked if blocked_keys.intersection(keys) else independent).append(workstream)
        recognized_keys = {rule.key for rule in _RULES}
        unrecognized_keys = tuple(sorted({fact.key for fact in facts if fact.key not in recognized_keys}))
        return RequirementConflictReport(
            tuple(conflicts), tuple(sorted(blocked)), tuple(sorted(independent)),
            len(facts), unrecognized_keys,
        )

    @staticmethod
    def record_decision(conflict: RequirementConflict, *, selected_option: str,
                        decision_maker: str, rationale: str, prior_plan_digest: str,
                        new_plan_revision: int) -> RequirementDecision:
        if not isinstance(selected_option, str) or selected_option not in {item.option_id for item in conflict.options}:
            raise ValueError("selected_option must be one of the conflict's explicit options")
        for name, value in (("decision_maker", decision_maker), ("rationale", rationale)):
            if not isinstance(value, str) or not value.strip() or not value.isprintable():
                raise ValueError(f"{name} must be non-empty printable text")
        if (not isinstance(prior_plan_digest, str) or len(prior_plan_digest) != 64
                or any(ch not in "0123456789abcdef" for ch in prior_plan_digest)):
            raise ValueError("prior_plan_digest must be a lowercase SHA-256 digest")
        if type(new_plan_revision) is not int or new_plan_revision < 1:
            raise ValueError("new_plan_revision must be a positive integer")
        body = {"conflict_id": conflict.conflict_id, "selected_option": selected_option,
                "decision_maker": decision_maker.strip(), "rationale": rationale.strip(),
                "prior_plan_digest": prior_plan_digest, "new_plan_revision": new_plan_revision}
        digest = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")).hexdigest()
        return RequirementDecision("requirement-decision:" + digest[:24], conflict.conflict_id,
            selected_option, decision_maker.strip(), rationale.strip(), prior_plan_digest,
            new_plan_revision, digest)

__all__ = ["ConflictOption", "RequirementConflict", "RequirementConflictAnalyzer",
           "RequirementConflictReport", "RequirementDecision", "RequirementFact"]
