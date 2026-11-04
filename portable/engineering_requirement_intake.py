"""Bounded, provenance-preserving intake of natural-language engineering requests.

This module extracts candidate statements only. It is not an NLP model, semantic
requirements oracle, or authorization boundary. The canonical design guard and
trusted planner still decide whether work may proceed.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_SCHEMA = "auren-engineering-requirement-intake-v1"
_MAX_INPUT_CHARS = 50_000
_MAX_STATEMENTS = 100
_MAX_STATEMENT_CHARS = 600

_LINE = re.compile(
    r"(?m)^[ \t]*(?:(?:[-*•]\s+)|(?:\d+[.)]\s+))?(?P<text>[^\r\n]+)"
)
_SENTENCE = re.compile(r".+?(?:[.!?;](?=(?:\s+[A-Z0-9“\"'-]|$))|$)")
_ASSUMPTION = re.compile(
    r"\b(assume|assuming|presume|presuming|probably|likely|if possible|"
    r"where possible|as needed|when available|subject to availability)\b",
    re.I,
)
_NON_GOAL = re.compile(
    r"\b(out of scope|not in scope|not required|exclude|excluded|"
    r"do not include|don't include|non.goal|not part of the mvp)\b",
    re.I,
)
_CONSTRAINT = re.compile(
    r"\b(must not|shall not|cannot|can't|do not|don't|never|without|"
    r"only|no (?:new |external |user )?[a-z][a-z -]{0,35})\b",
    re.I,
)
_REQUIREMENT = re.compile(
    r"\b(must|shall|should|required|needs? to|need to|build|create|"
    r"implement|provide|support|include|add|preserve|ensure|prevent|"
    r"reject|deny|allow|deliver|ship|display|show|fetch|integrate|"
    r"compare|recommend|record|retain|verify|keep)\b",
    re.I,
)
_ACCEPTANCE = re.compile(
    r"\b(acceptance criteria|given|when|then|verify|test|tests|passes?|"
    r"returns?|displays?|shows?|rejects?|denies?|must show|must return)\b",
    re.I,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\\b(api[\\s_-]?key|secret|token|password|private[\\s_-]?key|authorization)\\b"
    r"\\s*[:=]\\s*(?:Bearer\\s+)?[^\\s,;]+"
)


def _redact_sensitive_text(value: str) -> tuple[str, int]:
    return _SECRET_ASSIGNMENT.subn(r"\\1=[REDACTED]", value)



def parse_engineering_request(intent: str) -> dict[str, Any]:
    """Extract bounded candidate statements while preserving source positions.

    Oversized input or statement counts are reported as incomplete. Callers must
    block execution rather than silently process a partial requirements list.
    """
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("engineering request must be non-empty text")
    if len(intent) > _MAX_INPUT_CHARS:
        raise ValueError(
            f"engineering request exceeds the {_MAX_INPUT_CHARS}-character intake limit"
        )

    source_digest = hashlib.sha256(intent.encode("utf-8")).hexdigest()
    sanitized_intent, sanitized_value_count = _redact_sensitive_text(intent)
    statements: list[dict[str, Any]] = []
    oversized: list[dict[str, int]] = []
    too_many = False

    for line_match in _LINE.finditer(intent):
        line = line_match.group("text")
        line_start = line_match.start("text")
        for sentence_match in _SENTENCE.finditer(line):
            raw = sentence_match.group(0)
            left_trim = len(raw) - len(raw.lstrip())
            right_trimmed = raw.rstrip()
            raw_text = right_trimmed.strip()
            if not raw_text:
                continue
            start = line_start + sentence_match.start() + left_trim
            end = start + len(raw_text)
            if len(raw_text) > _MAX_STATEMENT_CHARS:
                oversized.append({"start": start, "end": end})
            if len(statements) >= _MAX_STATEMENTS:
                too_many = True
                break
            text, redaction_count = _redact_sensitive_text(raw_text)

            kinds: list[str] = []
            if _ASSUMPTION.search(text):
                kinds.append("assumption")
            if _NON_GOAL.search(text):
                kinds.append("non_goal")
            if _CONSTRAINT.search(text):
                kinds.append("constraint")
            if _REQUIREMENT.search(text):
                kinds.append("requirement")
            if _ACCEPTANCE.search(text):
                kinds.append("acceptance_criterion")
            if not kinds:
                kinds.append("context")

            statements.append({
                "index": len(statements),
                "text": text,
                "start": start,
                "end": end,
                "statement_digest": None if redaction_count else hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                "redacted": redaction_count > 0,
                "redaction_count": redaction_count,
                "kinds": kinds,
                "source": "user_intent",
            })
        if too_many:
            break

    complete = not oversized and not too_many
    candidates: dict[str, list[str]] = {
        "requirements": [],
        "assumptions": [],
        "constraints": [],
        "non_goals": [],
        "acceptance_criteria": [],
        "context": [],
    }
    for statement in statements:
        text = statement["text"]
        for kind in statement["kinds"]:
            key = "context" if kind == "context" else (
                "acceptance_criteria" if kind == "acceptance_criterion" else
                "non_goals" if kind == "non_goal" else
                "assumptions" if kind == "assumption" else
                "constraints" if kind == "constraint" else "requirements"
            )
            if text not in candidates[key]:
                candidates[key].append(text)

    payload: dict[str, Any] = {
        "schema_version": _SCHEMA,
        "source_digest": source_digest,
        "source_length": len(intent),
        "sanitized_intent": sanitized_intent,
        "sanitized_value_count": sanitized_value_count,
        "status": "candidate_extraction",
        "complete": complete,
        "statements": statements,
        **candidates,
        "oversized_statement_spans": oversized,
        "statement_limit_exceeded": too_many,
        "redacted_statement_count": sum(1 for item in statements if item["redacted"]),
        "redacted_value_count": sum(item["redaction_count"] for item in statements),
        "requirements_verified": False,
        "semantic_review_required": True,
        "limitations": (
            "Deterministic sentence and keyword extraction only. It does not prove "
            "semantic completeness, resolve ambiguity, infer authorization, or "
            "replace planner and independent reviewer analysis."
        ),
    }
    payload["digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return payload
