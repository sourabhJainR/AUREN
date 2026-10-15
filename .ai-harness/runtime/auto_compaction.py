#!/usr/bin/env python3
"""Deterministic context compaction with protected evidence.

Compaction happens at the harness boundary, before provider invocation. The
algorithm preserves contract/evidence sections, removes repeated material and
keeps the most recent operational state within a hard character budget.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

PROTECTED_HEADINGS = (
    "GOAL", "BOUNDARIES", "ACCEPTANCE", "SECURITY/PERMISSIONS",
    "CURRENT STATE", "TASK CONTRACT", "EVIDENCE", "VERIFY", "RISKS",
)


@dataclass(frozen=True)
class CompactionResult:
    text: str
    original_chars: int
    compacted_chars: int
    removed_chars: int
    digest: str
    compacted: bool


def _sections(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    current = "BODY"
    buf: list[str] = []
    for line in lines:
        match = re.match(r"^#{1,4}\s+(.+?)\s*$", line)
        if match:
            if buf:
                sections.append((current, "\n".join(buf).strip()))
            current = match.group(1).strip().upper()
            buf = []
        else:
            buf.append(line)
    if buf:
        sections.append((current, "\n".join(buf).strip()))
    return [(name, body) for name, body in sections if body]


def _dedupe_lines(text: str) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        key = re.sub(r"\s+", " ", line.strip()).lower()
        if key and len(key) > 24 and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(line)
    return "\n".join(out).strip()


def compact(text: str, *, budget_chars: int = 12000) -> CompactionResult:
    source = text.strip()
    budget = max(1000, int(budget_chars))
    if len(source) <= budget:
        return CompactionResult(
            source,
            len(source),
            len(source),
            0,
            hashlib.sha256(source.encode("utf-8")).hexdigest()[:16],
            False,
        )

    protected: list[tuple[str, str]] = []
    ordinary: list[tuple[str, str]] = []
    for name, body in _sections(source):
        (protected if any(token in name for token in PROTECTED_HEADINGS) else ordinary).append((name, body))

    selected: list[str] = []
    used = 0
    protected_names = {name for name, _ in protected}
    for name, body in protected + list(reversed(ordinary)):
        # Protected contract/evidence is never deduplicated or truncated.
        cleaned = body if name in protected_names else _dedupe_lines(body)
        block = f"## {name}\n{cleaned}".strip()
        if not block:
            continue
        if used + len(block) + 2 <= budget:
            selected.append(block)
            used += len(block) + 2
            continue
        if name in protected_names:
            raise ValueError(
                "protected context sections exceed the compaction budget; "
                "split the task or raise the budget instead of truncating required constraints"
            )
        remaining = budget - used - 80
        if remaining > 200:
            selected.append(block[:remaining].rstrip() + "\n[section compacted]")
        break

    result = "\n\n".join(selected).strip()
    digest = hashlib.sha256(result.encode("utf-8")).hexdigest()[:16]
    return CompactionResult(
        result,
        len(source),
        len(result),
        max(0, len(source) - len(result)),
        digest,
        True,
    )
