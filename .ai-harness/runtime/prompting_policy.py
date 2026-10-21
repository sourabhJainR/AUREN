#!/usr/bin/env python3
"""Provider-neutral prompting practices for autonomous coding runs."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PromptQuality:
    has_task: bool
    has_context: bool
    has_why: bool
    has_exit_criteria: bool
    has_guardrails: bool
    has_response_contract: bool
    needs_interview: bool

    @property
    def complete(self) -> bool:
        return all((self.has_task, self.has_context, self.has_why,
                    self.has_exit_criteria, self.has_guardrails,
                    self.has_response_contract))


def _has_heading(text: str, names: str) -> bool:
    """Recognize Markdown H1-H3 headings so prompt format is not brittle."""
    return bool(re.search(r"^#{1,3}\s*(?:" + names + r")\s*$", text, re.IGNORECASE | re.MULTILINE))


def assess_prompt(prompt: str) -> PromptQuality:
    lower = str(prompt).lower()
    task = _has_heading(lower, r"task|goal|job")
    context = _has_heading(lower, r"context|repository|background|profile")
    why = _has_heading(lower, r"why|intent|purpose|outcome")
    done = _has_heading(lower, r"exit criteria|acceptance|done|completion")
    guardrails = _has_heading(lower, r"guardrails|constraints|boundaries|non-goals|non-negotiable")
    response = _has_heading(lower, r"response contract|output contract|deliverable|report format")
    return PromptQuality(task, context, why, done, guardrails, response,
                         needs_interview=task and (not why or not done))


def compose(prompt: str) -> str:
    """Add the prompting contract without changing the user's task."""
    quality = assess_prompt(prompt)
    interview = (
        "If material information is missing and the ambiguity changes the solution, "
        "stop before making mutating changes and return `CLARIFICATION_NEEDED` with "
        "the smallest set of high-value questions. Do not ask questions whose answer "
        "can be established safely from the repository."
        if quality.needs_interview else
        "Do not start an interview when the repository and task contract already provide the needed information."
    )
    return prompt.rstrip() + "\n\n" + """## Prompting contract
Treat this as one complete job, not a sequence of instructions. Understand the task,
intent, constraints, repository context, and desired end state before choosing an approach.

## Why this matters
Use the stated intent and constraints to resolve ambiguity. Prefer the reason behind a
rule over a mechanical prohibition; when a local decision is needed, preserve the goal,
protected behavior, security boundary, and compatibility requirements.

## What done looks like
Finish when the task contract, acceptance criteria, and required evidence are satisfied.
Do not expand the job because you notice interesting adjacent improvements. Keep the final
response focused on outcome, changed scope, evidence, verification status, and any blockers.

## Response contract
Be concise and decision-oriented. Report only information needed to understand what was
changed or discovered, why the chosen approach satisfies the task, and what evidence exists.
Do not narrate every intermediate thought or repeat the task statement.

## Communication and data format
Use clear, controlled technical English. Prefer short sentences, active voice, consistent
terms, and one main instruction per sentence. Define acronyms when the reader may not know
them. Separate verified facts, assumptions, recommendations, and unknowns. Name the checked
revision and link evidence for material status claims. A skipped check is not a pass.
For structured model-facing data, prefer TOON when the receiver supports it and the schema
fits. Keep JSON when an API, configuration file, CLI contract, or receiver requires JSON.
Do not change a wire format without a versioned contract, round-trip tests, and a rollback path.
Do not expose secrets or bypass authority, privacy, or verification rules in any format.

## Verification economy
Do not spend model tokens on redundant requests to "double-check" or repeat verification.
The harness owns deterministic tests, diff checks, security gates, and acceptance evidence;
use their results as evidence and correct failures when required.

## Clarification policy
""" + interview + "\n"""


def quality_dict(prompt: str) -> dict[str, object]:
    q = assess_prompt(prompt)
    return {
        "has_task": q.has_task,
        "has_context": q.has_context,
        "has_why": q.has_why,
        "has_exit_criteria": q.has_exit_criteria,
        "has_guardrails": q.has_guardrails,
        "has_response_contract": q.has_response_contract,
        "needs_interview": q.needs_interview,
        "complete": q.complete,
    }
