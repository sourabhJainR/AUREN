#!/usr/bin/env python3
"""Small, dependency-free second-brain primitives for AUREN."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

VERSION = "1.1"
MEMORY_KINDS = {"fact", "decision", "lesson", "preference", "outcome"}
IMMUTABLE_KINDS = {"identity", "guardrail"}


def _id(prefix: str, payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def create_memory(*, kind: str, text: str, source: str, evidence_ids: list[str] | None = None, confidence: float = 0.0, task_scope: str | None = None, intent_digest: str | None = None) -> dict[str, Any]:
    kind = kind.strip().lower(); text = text.strip(); source = source.strip()
    if kind not in MEMORY_KINDS | IMMUTABLE_KINDS: raise ValueError(f"unsupported memory kind: {kind}")
    if not text or not source: raise ValueError("memory text and source are required")
    evidence = sorted({x.strip() for x in (evidence_ids or []) if x.strip()})
    item = {"version": VERSION, "kind": kind, "text": text, "source": source, "evidence_ids": evidence, "confidence": max(0.0, min(1.0, float(confidence))), "task_scope": task_scope, "intent_digest": intent_digest, "created_at": int(time.time())}
    item["id"] = _id("memory", {k: v for k, v in item.items() if k != "created_at"})
    return item


def validate_memory(item: dict[str, Any], *, expected_intent_digest: str | None = None) -> list[str]:
    errors: list[str] = []
    if item.get("kind") not in MEMORY_KINDS | IMMUTABLE_KINDS: errors.append("unsupported_kind")
    if not str(item.get("text", "")).strip(): errors.append("missing_text")
    if not str(item.get("source", "")).strip(): errors.append("missing_source")
    if item.get("kind") in {"fact", "decision", "lesson", "outcome"} and not item.get("evidence_ids"): errors.append("missing_evidence")
    if expected_intent_digest and item.get("intent_digest") not in {None, expected_intent_digest}: errors.append("intent_mismatch")
    return errors


def persist_memory(path: Path, item: dict[str, Any]) -> dict[str, Any]:
    """Validate and durably append one memory item with an fsync boundary."""
    errors = validate_memory(item)
    if errors: raise ValueError("invalid memory: " + ",".join(errors))
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"); handle.flush(); os.fsync(handle.fileno())
    return item


def rank_memory(items: list[dict[str, Any]], *, query_terms: set[str] | None = None, limit: int = 10) -> list[dict[str, Any]]:
    terms = {x.lower() for x in (query_terms or set()) if x.strip()}
    def score(item: dict[str, Any]) -> tuple[float, int, str]:
        text = str(item.get("text", "")).lower(); lexical = sum(1 for term in terms if term in text)
        return (lexical + float(item.get("confidence", 0.0)), int(item.get("created_at", 0)), str(item.get("id", "")))
    valid = [x for x in items if not validate_memory(x)]; valid.sort(key=score, reverse=True)
    return valid[: max(0, min(100, int(limit)))]


def heartbeat_suggestions(*, tasks: list[dict[str, Any]], recent_outcomes: list[dict[str, Any]], now: int | None = None, max_suggestions: int = 5) -> list[dict[str, Any]]:
    now = int(time.time()) if now is None else int(now); suggestions: list[dict[str, Any]] = []
    outcome_text = " ".join(str(x.get("text", "")) for x in recent_outcomes).lower()
    for task in tasks:
        if str(task.get("status", "open")).lower() in {"done", "closed", "cancelled"}: continue
        title = str(task.get("title", "untitled")).strip(); reason = "Open task is present in the local work queue."
        if any(word in outcome_text for word in ("blocked", "failed", "regression")): reason = "Recent outcomes contain a failure signal; review this open task before starting new work."
        suggestions.append({"id": _id("suggestion", {"title": title, "reason": reason, "now": now}), "type": "review_or_prepare", "title": title, "reason": reason, "mode": "suggestion"})
    return suggestions[: max(0, min(20, int(max_suggestions)))]


def load_local_memory(path: Path, *, query_terms: set[str] | None = None, limit: int = 10) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists(): return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try: item = json.loads(line)
        except json.JSONDecodeError: continue
        if isinstance(item, dict): rows.append(item)
    return rank_memory(rows, query_terms=query_terms, limit=limit)
