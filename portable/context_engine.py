"""Small, provider-neutral context engineering primitives for AUREN.

The rule is simple: agents do not pass their whole conversation to the next
agent. They pass a bounded handoff containing only task-relevant information.
No model call is required for packing, ranking, deduplication or compaction.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class ContextItem:
    kind: str
    text: str
    source: str = ""
    priority: int = 50
    verified: bool = False
    refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextPolicy:
    max_chars: int = 9000
    max_items: int = 18
    max_item_chars: int = 2400
    output_chars: int = 4200

    def validate(self) -> None:
        if min(self.max_chars, self.max_items, self.max_item_chars, self.output_chars) < 1:
            raise ValueError("context budgets must be positive")


@dataclass(frozen=True)
class Handoff:
    task_id: str
    sender: str
    receiver: str
    objective: str
    completed: str = ""
    decisions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    next_action: str = ""

    def render(self, max_chars: int = 4200) -> str:
        sections = [
            ("OBJECTIVE", self.objective),
            ("COMPLETED", self.completed),
            ("DECISIONS", "\n".join(f"- {x}" for x in self.decisions)),
            ("EVIDENCE", "\n".join(f"- {x}" for x in self.evidence)),
            ("RISKS", "\n".join(f"- {x}" for x in self.risks)),
            ("FILES", "\n".join(f"- {x}" for x in self.files)),
            ("NEXT", self.next_action),
        ]
        text = "\n".join(f"## {name}\n{value}" for name, value in sections if value.strip())
        return _compact(text, max_chars)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.render(12000).encode("utf-8")).hexdigest()


class ContextEngine:
    """Deterministically pack the smallest useful context for an agent."""

    def __init__(self, policy: ContextPolicy | None = None) -> None:
        self.policy = policy or ContextPolicy()
        self.policy.validate()

    @staticmethod
    def _key(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    def select(self, items: Iterable[ContextItem], *, required: Sequence[str] = ()) -> list[ContextItem]:
        required_keys = {self._key(x) for x in required if x.strip()}
        unique: dict[str, ContextItem] = {}
        for item in items:
            text = _compact(item.text, self.policy.max_item_chars).strip()
            if not text:
                continue
            key = self._key(text)
            candidate = ContextItem(item.kind, text, item.source, item.priority, item.verified, tuple(sorted(set(item.refs))))
            old = unique.get(key)
            if old is None or self._rank(candidate) > self._rank(old):
                unique[key] = candidate

        ranked = sorted(unique.values(), key=self._rank, reverse=True)
        selected: list[ContextItem] = []
        used = 0
        for item in ranked:
            mandatory = self._key(item.text) in required_keys
            cost = len(item.text) + len(item.kind) + 4
            if not mandatory and (len(selected) >= self.policy.max_items or used + cost > self.policy.max_chars):
                continue
            if mandatory and used + cost > self.policy.max_chars:
                # Required context wins, but is still individually bounded.
                if selected:
                    selected.pop()
                    used = sum(len(x.text) + len(x.kind) + 4 for x in selected)
                if used + cost > self.policy.max_chars:
                    continue
            selected.append(item)
            used += cost
            if len(selected) >= self.policy.max_items:
                break
        return selected

    @staticmethod
    def _rank(item: ContextItem) -> tuple[int, int, int, str]:
        kind_weight = {"contract": 100, "handoff": 90, "decision": 80, "evidence": 70, "risk": 65, "file": 50, "output": 20}
        return (kind_weight.get(item.kind, 40), 1 if item.verified else 0, int(item.priority), item.source)

    def pack(self, items: Iterable[ContextItem], *, required: Sequence[str] = ()) -> str:
        selected = self.select(items, required=required)
        if not selected:
            return "No additional context selected."
        blocks = []
        for item in selected:
            refs = f" [{', '.join(item.refs)}]" if item.refs else ""
            blocks.append(f"### {item.kind}{refs}\n{item.text}")
        return _compact("\n\n".join(blocks), self.policy.max_chars)

    def from_memory(self, rows: Iterable[Mapping[str, object]], *, limit: int | None = None) -> list[ContextItem]:
        items: list[ContextItem] = []
        for row in rows:
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            items.append(ContextItem(
                kind=str(row.get("kind", "memory")),
                text=text,
                source=str(row.get("agent", "memory")),
                priority=int(float(row.get("confidence", 0.0)) * 100),
                verified=bool(row.get("verified", False)),
                refs=tuple(str(x) for x in row.get("evidence", []) if x),
            ))
        return items[-limit:] if limit else items


def handoff_from_output(*, task_id: str, sender: str, receiver: str, objective: str,
                        output: str, files: Sequence[str] = (), success: bool = True,
                        max_chars: int = 3600) -> Handoff:
    """Create a bounded handoff without requiring an LLM summarizer.

    The receiver gets the output once, bounded and labelled. This intentionally
    avoids creating a second model-generated summary that can drift from source.
    """
    clean = _compact(output.strip(), max_chars)
    status = "completed" if success else "failed; inspect the evidence before proceeding"
    return Handoff(task_id, sender, receiver, objective, completed=status,
                   evidence=(clean,) if clean else (), files=tuple(files),
                   next_action=f"Continue {receiver} using the evidence above; do not repeat completed work.")


def _compact(text: str, limit: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= limit:
        return text
    # Preserve both the opening contract/facts and the final outcome.
    head = max(400, limit // 2)
    tail = max(200, limit - head - 80)
    return text[:head].rstrip() + "\n...[context compacted]...\n" + text[-tail:].lstrip()


__all__ = ["ContextEngine", "ContextItem", "ContextPolicy", "Handoff", "handoff_from_output"]
