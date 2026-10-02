#!/usr/bin/env python3
"""Executable context acquisition pipeline with bounded recovery and one evidence envelope."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from .context_broker import ContextBroker, ContextCandidate
    from .context_planner import (
        EvidenceCandidate,
        choose_retrieval_recovery,
        plan_context,
        select_evidence,
    )
    from .task_memory import record as record_task_observation, relevant as relevant_task_memory
    from portable.repository_intelligence import RepositoryIntelligence
    from portable.semantic_addressing import SymbolLocator
except ImportError:
    from context_broker import ContextBroker, ContextCandidate
    from context_planner import (
        EvidenceCandidate,
        choose_retrieval_recovery,
        plan_context,
        select_evidence,
    )
    from task_memory import record as record_task_observation, relevant as relevant_task_memory
    from portable.repository_intelligence import RepositoryIntelligence
    from portable.semantic_addressing import SymbolLocator


@dataclass(frozen=True, slots=True)
class ContextEvidenceItem:
    evidence_id: str
    kind: str
    source: str
    text: str
    relevance: float
    confidence: float
    freshness: float
    digest: str


@dataclass(frozen=True, slots=True)
class ContextEvidence:
    """Immutable context/evidence envelope carried across execution and rollout."""

    task_id: str
    query: str
    phase: str
    risk: str
    intent_digest: str
    context_plan_digest: str
    repository_snapshot_digest: str
    selected_paths: tuple[str, ...]
    symbol_refs: tuple[str, ...]
    graph_paths: tuple[str, ...]
    items: tuple[ContextEvidenceItem, ...]
    evidence_digest: str
    token_estimate: int
    unknowns: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def deployment_binding(self) -> dict[str, str]:
        return {
            "intent_digest": self.intent_digest,
            "repository_snapshot_digest": self.repository_snapshot_digest,
            "context_plan_digest": self.context_plan_digest,
            "evidence_digest": self.evidence_digest,
        }

    def engineering_envelope(self):
        """Create the canonical lifecycle envelope for this context snapshot."""
        from portable.engineering_evidence_envelope import EngineeringEvidenceEnvelope
        return EngineeringEvidenceEnvelope.from_context_evidence(self)


class ContextAcquisitionPipeline:
    """DISCOVER -> SCORE -> LEASE -> USE -> COMPRESS -> RELEASE.

    Retrieval recovery is intentionally bounded. A failed path is not retried
    with the same strategy during the acquisition; the next attempt must use a
    different retrieval mode or the pipeline stops.
    """

    _RETRIEVAL_MODES = ("semantic", "structural", "lexical", "history", "pack")

    def __init__(
        self,
        root: str | Path,
        *,
        budget_chars: int | None = None,
        max_items: int | None = None,
        decision_advisor: Callable[[dict[str, Any]], tuple[str, float] | None] | None = None,
        decision_confidence_threshold: float = 0.75,
    ) -> None:
        self.root = Path(root).resolve()
        self.repository = RepositoryIntelligence(self.root)
        self.decision_advisor = decision_advisor
        self.decision_confidence_threshold = max(0.0, min(1.0, decision_confidence_threshold))
        self._broker = ContextBroker(
            budget_chars=budget_chars if budget_chars is not None else 14000,
            max_items=max_items if max_items is not None else 18,
        )

    def acquire(
        self,
        *,
        task_id: str,
        query: str,
        phase: str,
        intent_digest: str,
        risk: str = "medium",
        uncertainty: str = "medium",
        policy_strategy: str | None = None,
        extra_evidence: Iterable[EvidenceCandidate] = (),
        pack: bool = False,
    ) -> ContextEvidence:
        plan = plan_context(
            phase=phase,
            risk=risk,
            uncertainty=uncertainty,
            policy_strategy=policy_strategy,
        )
        candidates: list[EvidenceCandidate] = list(extra_evidence)
        candidates.extend(self._failure_memory(query))

        failed_modes: list[str] = []
        retrieval_unknowns: list[str] = []
        retrieved = None
        selected_mode = None

        available = [mode for mode in plan.retrieval_modes if mode in self._RETRIEVAL_MODES]
        if "semantic" not in available:
            available.insert(0, "semantic")
        # pack=True is an explicit supplemental-context request. Preserve the
        # historical behavior by executing it once in addition to targeted
        # retrieval; it is not a fallback candidate that early success can skip.
        if pack:
            try:
                pack_result = self._retrieve("pack", query, plan.budget, plan.max_items)
                if pack_result["evidence"]:
                    candidates.extend(pack_result["evidence"])
                else:
                    retrieval_unknowns.append("requested pack produced no usable evidence")
                    self._record_retrieval(task_id, "pack", "failed", "no usable evidence")
            except Exception as exc:
                safe_error = self._safe_text(f"{type(exc).__name__}: {exc}")
                retrieval_unknowns.append(f"requested pack failed: {safe_error}")
                self._record_retrieval(task_id, "pack", "failed", safe_error)

        # Pack is supplemental, not a recovery mode.
        available = [mode for mode in available if mode != "pack"]
        working_modes = tuple(mode for mode in self._working_modes(query) if mode in available)

        while True:
            recovery = self._choose_recovery(
                task_id=task_id,
                query=query,
                phase=phase,
                risk=risk,
                uncertainty=uncertainty,
                failed_modes=failed_modes,
                available_modes=available,
                working_modes=working_modes,
            )
            if recovery.terminal:
                retrieval_unknowns.append(recovery.reason)
                break

            mode = str(recovery.mode)
            try:
                result = self._retrieve(mode, query, plan.budget, plan.max_items)
            except Exception as exc:  # retrieval is an evidence boundary; do not hide the failure
                failed_modes.append(mode)
                safe_error = self._safe_text(f"{type(exc).__name__}: {exc}")
                retrieval_unknowns.append(f"{mode} retrieval failed: {safe_error}")
                self._record_retrieval(task_id, mode, "failed", safe_error)
                continue

            if not result["evidence"]:
                failed_modes.append(mode)
                retrieval_unknowns.append(f"{mode} retrieval produced no usable evidence")
                self._record_retrieval(task_id, mode, "failed", "no usable evidence")
                continue

            selected_mode = mode
            retrieved = result
            candidates.extend(result["evidence"])
            self._record_retrieval(
                task_id,
                mode,
                "worked",
                json.dumps(
                    {
                        "evidence_count": len(result["evidence"]),
                        "paths": result.get("paths", ()),
                    },
                    sort_keys=True,
                ),
            )
            break

        if retrieved is None:
            # History itself can still be useful even when every repository path
            # failed. It is not a silent success: the unknowns stay in the envelope.
            history_evidence = [item for item in candidates if item.kind == "failure-memory"]
            if history_evidence:
                selected_mode = "history"
                candidates.extend(history_evidence)

        selected = select_evidence(candidates, budget=plan.budget, max_items=plan.max_items)
        self._broker.register_many(
            ContextCandidate(
                item.evidence_id,
                item.kind,
                f"selected:{item.source}",
                lambda text=item.text: text,
                relevance=item.relevance,
                confidence=item.confidence,
                freshness=item.freshness,
                cost=item.cost,
                phase=phase,
            )
            for item in selected
        )
        leases = self._broker.discover(
            query,
            phase=phase,
            budget_chars=plan.budget,
            max_items=plan.max_items,
        )
        by_id = {item.evidence_id: item for item in selected}
        items = tuple(
            ContextEvidenceItem(
                lease.context_id,
                lease.kind,
                by_id[lease.context_id].source,
                lease.text,
                round(lease.score, 6),
                by_id[lease.context_id].confidence,
                by_id[lease.context_id].freshness,
                lease.digest,
            )
            for lease in leases
            if lease.context_id in by_id
        )

        snapshot_digest = retrieved["snapshot"] if retrieved else self.repository.digest()
        selected_paths = tuple(retrieved.get("paths", ())) if retrieved else ()
        graph_paths = tuple(retrieved.get("graph_paths", ())) if retrieved else ()
        symbol_refs = tuple(self._resolve_symbols(SymbolLocator(self.repository.index), query))
        provider_unknowns = tuple(retrieved.get("unknowns", ())) if retrieved else ()
        unknowns = tuple(dict.fromkeys((*retrieval_unknowns, *provider_unknowns, *self._historical_unknowns(query))))

        plan_digest = _digest(
            {
                "phase": plan.phase,
                "modes": plan.retrieval_modes,
                "budget": plan.budget,
                "max_items": plan.max_items,
                "fresh": plan.require_fresh_verification,
                "strategy": plan.policy_strategy,
                "selected_mode": selected_mode,
            }
        )
        evidence_digest = _digest(
            {
                "task_id": task_id,
                "query": query,
                "intent_digest": intent_digest,
                "context_plan_digest": plan_digest,
                "repository_snapshot_digest": snapshot_digest,
                "items": [asdict(item) for item in items],
            }
        )
        return ContextEvidence(
            str(task_id),
            str(query),
            plan.phase,
            str(risk).lower(),
            str(intent_digest),
            plan_digest,
            snapshot_digest,
            selected_paths,
            symbol_refs,
            graph_paths,
            items,
            evidence_digest,
            sum(max(1, len(item.text.split())) for item in items),
            unknowns,
        )

    def release(self, evidence: ContextEvidence) -> None:
        self._broker.release(item.evidence_id for item in evidence.items)

    def refresh(self) -> None:
        self.repository.refresh()

    def _choose_recovery(
        self,
        *,
        task_id: str,
        query: str,
        phase: str,
        risk: str,
        uncertainty: str,
        failed_modes: list[str],
        available_modes: list[str],
        working_modes: tuple[str, ...],
    ):
        """Use an optional typed decision advisor, then enforce deterministic policy."""
        if self.decision_advisor is not None:
            state = {
                "task_id": str(task_id),
                "query": self._safe_text(query),
                "phase": str(phase),
                "risk": str(risk).lower(),
                "uncertainty": str(uncertainty).lower(),
                "failed_modes": tuple(failed_modes),
                "available_modes": tuple(available_modes),
                "working_modes": tuple(working_modes),
            }
            try:
                advised = self.decision_advisor(state)
                if advised is not None:
                    mode, confidence = advised
                    mode = str(mode).strip().lower()
                    confidence = float(confidence)
                    if (
                        mode in available_modes
                        and mode not in failed_modes
                        and confidence >= self.decision_confidence_threshold
                    ):
                        return choose_retrieval_recovery(
                            failed_modes=failed_modes,
                            available_modes=(mode, *available_modes),
                            working_modes=(),
                        )
            except (TypeError, ValueError, RuntimeError):
                # A decision advisor is advisory. Provider failure or malformed
                # output must never break deterministic context acquisition.
                pass

        return choose_retrieval_recovery(
            failed_modes=failed_modes,
            available_modes=available_modes,
            working_modes=working_modes,
        )

    def _retrieve(self, mode: str, query: str, budget: int, max_items: int) -> dict[str, Any]:
        if mode == "history":
            rows = relevant_task_memory(self.root, query, limit=min(12, max_items))
            evidence = [
                EvidenceCandidate(
                    f"memory:{row['id']}",
                    "history",
                    self._history_text(row),
                    relevance=0.92 if row.get("outcome") in {"failed", "regressed"} else 0.75,
                    confidence=0.95 if row.get("promotion") == "verified" else 0.70,
                    freshness=0.85,
                    cost=max(1, len(str(row.get("detail", "")))),
                    source="task-memory",
                )
                for row in rows
            ]
            return {
                "evidence": evidence,
                "snapshot": self.repository.digest(),
                "paths": (),
                "graph_paths": (),
                "unknowns": (),
            }
        if mode == "pack":
            result = self.repository.pack(
                token_budget=max(1, min(budget // 4, 4000)),
                compress=True,
            )
            evidence = [
                EvidenceCandidate(
                    f"pack:{result.snapshot_digest[:16]}",
                    "repository-pack",
                    result.as_text(),
                    relevance=0.55,
                    confidence=0.95,
                    freshness=1.0,
                    cost=max(1, result.token_estimate),
                    source="RepositoryIntelligence.pack",
                )
            ]
            return {
                "evidence": evidence,
                "snapshot": result.snapshot_digest,
                "paths": tuple(file.path for file in result.files),
                "graph_paths": (),
            }

        symbols = self._resolve_symbols(SymbolLocator(self.repository.index), query)
        if mode == "structural":
            retrieval_query = " ".join(symbols[:12]) or query
            result = self.repository.retrieve(
                retrieval_query,
                token_budget=max(1, min(budget // 3, 6000)),
                max_files=min(max_items, 8),
                context_lines=12,
                graph_hops=1,
            )
        elif mode == "lexical":
            terms = tuple(dict.fromkeys(
                token.strip(".,:;()[]{}")
                for token in query.split()
                if len(token.strip(".,:;()[]{}")) >= 3
            ))
            retrieval_query = " ".join(terms[:24]) or query
            result = self.repository.retrieve(
                retrieval_query,
                token_budget=max(1, min(budget // 3, 5000)),
                max_files=min(max_items, 8),
                context_lines=8,
                graph_hops=0,
            )
        else:
            result = self.repository.retrieve(
                query,
                token_budget=max(1, min(budget, 8000)),
                max_files=min(max_items, 12),
                context_lines=20,
                graph_hops=2,
            )

        evidence = [
            EvidenceCandidate(
                f"code:{chunk.path}:{chunk.start_line}:{chunk.end_line}:{mode}",
                "semantic" if mode == "semantic" else "structural",
                self._safe_text(chunk.text),
                max(0.0, min(1.0, chunk.score / 10.0)),
                0.9,
                1.0,
                max(1, chunk.tokens),
                chunk.path,
            )
            for chunk in result.chunks
        ]
        if result.graph_trace.expanded_paths:
            evidence.append(
                EvidenceCandidate(
                    f"graph:{result.snapshot_digest[:16]}:{mode}",
                    "graph",
                    json.dumps(result.graph_trace.as_dict(), sort_keys=True),
                    0.8,
                    0.9,
                    1.0,
                    max(1, len(result.graph_trace.expanded_paths) * 8),
                    "CodebaseIndex.graph",
                )
            )
        return {
            "evidence": evidence,
            "snapshot": result.snapshot_digest,
            "paths": result.relevant_paths,
            "graph_paths": result.graph_trace.expanded_paths,
            "unknowns": tuple(result.unknowns),
        }

    def _failure_memory(self, query: str) -> list[EvidenceCandidate]:
        rows = relevant_task_memory(self.root, query, limit=20)
        candidates: list[EvidenceCandidate] = []
        for row in rows:
            if row.get("outcome") not in {"failed", "regressed"}:
                continue
            detail = self._history_text(row)
            candidates.append(
                EvidenceCandidate(
                    f"failure:{row['id']}",
                    "failure-memory",
                    detail,
                    relevance=0.98,
                    confidence=0.95 if row.get("promotion") == "verified" else 0.75,
                    freshness=0.95,
                    cost=max(1, len(detail)),
                    source="task-memory",
                )
            )
        return candidates

    def _history_text(self, row: dict[str, Any]) -> str:
        return "\n".join(
            part
            for part in (
                f"Historical {str(row.get('outcome', 'unknown')).upper()} retrieval/engineering observation.",
                f"Task: {self._safe_text(str(row.get('task', '')))}",
                f"Approach: {self._safe_text(str(row.get('approach', '')))}",
                f"Detail: {self._safe_text(str(row.get('detail', '')))}",
                f"Evidence: {self._safe_text(', '.join(row.get('evidence_ids', [])))}",
            )
            if part.strip()
        )

    def _working_modes(self, query: str) -> tuple[str, ...]:
        # Learned preference is advisory only. Require repeated evidence and
        # a healthy observed success ratio so one lucky run cannot steer the
        # retrieval policy (history-overfit guard).
        rows = relevant_task_memory(self.root, "context retrieval", limit=100)
        stats: dict[str, list[int]] = {}
        for row in rows:
            approach = str(row.get("approach", ""))
            if not approach.startswith("context-retrieval:"):
                continue
            mode = approach.split(":", 1)[1]
            bucket = stats.setdefault(mode, [0, 0])
            if row.get("outcome") in {"worked", "passed", "success"}:
                bucket[0] += 1
            elif row.get("outcome") in {"failed", "regressed"}:
                bucket[1] += 1

        ranked = [
            (
                mode,
                successes,
                failures,
                successes / float(successes + failures),
            )
            for mode, (successes, failures) in stats.items()
            if successes >= 3
            and successes + failures >= 3
            and successes / float(successes + failures) >= 0.75
        ]
        ranked.sort(key=lambda item: (-item[3], -item[1], item[0]))
        return tuple(mode for mode, _successes, _failures, _ratio in ranked)

    def _historical_unknowns(self, query: str) -> tuple[str, ...]:
        rows = relevant_task_memory(self.root, query, limit=20)
        return tuple(
            f"prior {row.get('outcome', 'unknown')} approach: {row.get('approach', '')}"
            for row in rows
            if row.get("outcome") in {"failed", "regressed"}
        )[:8]

    def _record_retrieval(self, task_id: str, mode: str, outcome: str, detail: str) -> None:
        record_task_observation(
            self.root,
            task=f"context retrieval: {mode}",
            category="verification",
            outcome="worked" if outcome == "worked" else "failed",
            detail=detail,
            approach=f"context-retrieval:{mode}",
            run_id=str(task_id),
            evidence_ids=(),
            source_agent="context-broker",
            promotion="candidate",
        )

    @staticmethod
    def _safe_text(text: str) -> str:
        """Redact credential-like values before context crosses the evidence boundary."""
        value = str(text)
        for pattern in _SENSITIVE_PATTERNS:
            value = pattern.sub(
                lambda match: f"{match.group(1)}=[REDACTED]" if match.lastindex else "[REDACTED]",
                value,
            )
        return value

    @staticmethod
    def _resolve_symbols(locator: SymbolLocator, query: str) -> list[str]:
        refs: list[str] = []
        for token in (x.strip(".,:()[]{}") for x in query.split()):
            if len(token) >= 3 and token[:1].isalpha():
                refs.extend(address.ref for address in locator.find(token))
        return sorted(set(refs))[:16]


_SENSITIVE_PATTERNS = (
    re.compile(r"(?is)(api[_-]?key|access[_-]?key|secret|password|passwd|pwd|token)\s*[:=]\s*['"]?[^\s'"]{8,}"),
    re.compile(r"(?is)(authorization)\s*[:=]\s*['"]?bearer\s+[^\s'"]+"),
    re.compile(r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


__all__ = ["ContextEvidence", "ContextEvidenceItem", "ContextAcquisitionPipeline"]
