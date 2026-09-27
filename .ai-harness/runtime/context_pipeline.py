#!/usr/bin/env python3
"""Executable context acquisition pipeline with one immutable evidence envelope."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import hashlib, json
from pathlib import Path
from typing import Any, Iterable
try:
    from .context_broker import ContextBroker, ContextCandidate
    from .context_planner import EvidenceCandidate, plan_context, select_evidence
    from portable.repository_intelligence import RepositoryIntelligence
    from portable.semantic_addressing import SymbolLocator
except ImportError:
    from context_broker import ContextBroker, ContextCandidate
    from context_planner import EvidenceCandidate, plan_context, select_evidence
    from portable.repository_intelligence import RepositoryIntelligence
    from portable.semantic_addressing import SymbolLocator
@dataclass(frozen=True, slots=True)
class ContextEvidenceItem:
    evidence_id: str; kind: str; source: str; text: str; relevance: float; confidence: float; freshness: float; digest: str
@dataclass(frozen=True, slots=True)
class ContextEvidence:
    """Immutable context/evidence envelope carried across execution and rollout."""
    task_id: str; query: str; phase: str; risk: str; intent_digest: str; context_plan_digest: str; repository_snapshot_digest: str; selected_paths: tuple[str, ...]; symbol_refs: tuple[str, ...]; graph_paths: tuple[str, ...]; items: tuple[ContextEvidenceItem, ...]; evidence_digest: str; token_estimate: int; unknowns: tuple[str, ...] = ()
    def as_dict(self) -> dict[str, Any]: return asdict(self)
    def deployment_binding(self) -> dict[str, str]: return {"intent_digest": self.intent_digest, "repository_snapshot_digest": self.repository_snapshot_digest, "context_plan_digest": self.context_plan_digest, "evidence_digest": self.evidence_digest}
    def engineering_envelope(self):
        """Create the canonical lifecycle envelope for this context snapshot."""
        from portable.engineering_evidence_envelope import EngineeringEvidenceEnvelope
        return EngineeringEvidenceEnvelope.from_context_evidence(self)
class ContextAcquisitionPipeline:
    """DISCOVER -> SCORE -> LEASE -> USE -> COMPRESS -> RELEASE."""
    def __init__(self, root: str | Path, *, budget_chars: int | None = None, max_items: int | None = None) -> None:
        self.root = Path(root).resolve(); self.repository = RepositoryIntelligence(self.root); self._broker = ContextBroker(budget_chars=budget_chars if budget_chars is not None else 14000, max_items=max_items if max_items is not None else 18)
    def acquire(self, *, task_id: str, query: str, phase: str, intent_digest: str, risk: str = "medium", uncertainty: str = "medium", policy_strategy: str | None = None, extra_evidence: Iterable[EvidenceCandidate] = (), pack: bool = False) -> ContextEvidence:
        plan = plan_context(phase=phase, risk=risk, uncertainty=uncertainty, policy_strategy=policy_strategy); semantic = self.repository.retrieve(query, token_budget=max(1, min(plan.budget, 8000)), max_files=min(plan.max_items, 12), context_lines=20, graph_hops=2); locator = SymbolLocator(self.repository.index); symbol_refs = self._resolve_symbols(locator, query)
        candidates: list[EvidenceCandidate] = list(extra_evidence)
        for chunk in semantic.chunks: candidates.append(EvidenceCandidate(f"code:{chunk.path}:{chunk.start_line}:{chunk.end_line}", "semantic", chunk.text, max(0.0, min(1.0, chunk.score / 10.0)), 0.9, 1.0, max(1, chunk.tokens), chunk.path))
        candidates.append(EvidenceCandidate("graph:" + semantic.snapshot_digest[:16], "graph", json.dumps(semantic.graph_trace.as_dict(), sort_keys=True), 0.8 if semantic.graph_trace.expanded_paths else 0.3, 0.9, 1.0, max(1, len(semantic.graph_trace.expanded_paths) * 8), "CodebaseIndex.graph"))
        if pack:
            broad = self.repository.pack(token_budget=max(1, min(plan.budget // 2, 6000)), compress=True); candidates.append(EvidenceCandidate("pack:" + broad.snapshot_digest[:16], "repository-pack", broad.as_text(), 0.5, 0.95, 1.0, max(1, broad.token_estimate), "RepositoryIntelligence.pack"))
        selected = select_evidence(candidates, budget=plan.budget, max_items=plan.max_items)
        self._broker.register_many(ContextCandidate(item.evidence_id, item.kind, f"selected:{item.source}", lambda text=item.text: text, relevance=item.relevance, confidence=item.confidence, freshness=item.freshness, cost=item.cost, phase=phase) for item in selected)
        leases = self._broker.discover(query, phase=phase, budget_chars=plan.budget, max_items=plan.max_items); by_id = {item.evidence_id: item for item in selected}
        items = tuple(ContextEvidenceItem(lease.context_id, lease.kind, by_id[lease.context_id].source, lease.text, round(lease.score, 6), by_id[lease.context_id].confidence, by_id[lease.context_id].freshness, lease.digest) for lease in leases if lease.context_id in by_id)
        plan_digest = _digest({"phase": plan.phase, "modes": plan.retrieval_modes, "budget": plan.budget, "max_items": plan.max_items, "fresh": plan.require_fresh_verification, "strategy": plan.policy_strategy})
        evidence_digest = _digest({"task_id": task_id, "query": query, "intent_digest": intent_digest, "context_plan_digest": plan_digest, "repository_snapshot_digest": semantic.snapshot_digest, "items": [asdict(item) for item in items]})
        return ContextEvidence(str(task_id), str(query), plan.phase, str(risk).lower(), str(intent_digest), plan_digest, semantic.snapshot_digest, semantic.relevant_paths, tuple(symbol_refs), semantic.graph_trace.expanded_paths, items, evidence_digest, sum(max(1, len(item.text.split())) for item in items), semantic.unknowns)
    def release(self, evidence: ContextEvidence) -> None: self._broker.release(item.evidence_id for item in evidence.items)
    def refresh(self) -> None: self.repository.refresh()
    @staticmethod
    def _resolve_symbols(locator: SymbolLocator, query: str) -> list[str]:
        refs: list[str] = []
        for token in (x.strip(".,:()[]{}") for x in query.split()):
            if len(token) >= 3 and token[:1].isalpha(): refs.extend(address.ref for address in locator.find(token))
        return sorted(set(refs))[:16]
def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
__all__ = ["ContextEvidence", "ContextEvidenceItem", "ContextAcquisitionPipeline"]
