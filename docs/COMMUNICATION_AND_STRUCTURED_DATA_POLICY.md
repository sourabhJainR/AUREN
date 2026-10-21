# Communication and Structured-Data Policy

**Status:** Required project policy  
**Applies to:** AUREN agents, maintainers, contributors, generated reports, issue and pull-request text, logs intended for people, support replies, release notes, and outbound messages.

## 1. Purpose

AUREN must communicate in clear, controlled English. It must choose a structured-data format that fits the receiver and the interface. These rules reduce ambiguity, wasted context, and unsafe interpretation.

This policy uses the principles of ASD-STE100 Simplified Technical English (STE) as a writing guide. It does not claim that AUREN, its documentation, or any output is certified or fully compliant with the complete ASD-STE100 specification. Use the official controlled specification when a contract requires formal compliance.

## 2. Plain-language rules

Apply these rules to internal and outbound communication.

1. Write short, direct sentences.
2. Put one main instruction or claim in each sentence.
3. Use the active voice when the actor is known.
4. Use approved terms consistently. Do not use different words for the same concept unless the difference matters.
5. Define an acronym at first use when the reader may not know it.
6. Use a specific name instead of “it”, “this”, or “they” when the reference may be unclear.
7. State conditions before the actions that depend on them.
8. Use numbered steps for procedures and bullets for independent facts.
9. State the expected result and the failure behavior for important instructions.
10. Separate verified facts, assumptions, recommendations, and unknowns.
11. State the source, revision, timestamp, or evidence reference for material status claims.
12. Avoid idioms, jokes, vague intensifiers, unnecessary adjectives, and marketing claims in technical instructions.
13. Do not hide a blocker behind positive wording. State what is blocked, why it is blocked, and the next safe action.
14. Do not claim that work is complete because a job started, a pull request exists, or a test was skipped.
15. Use dates with an unambiguous format and include the time zone when the time matters.

## 3. Required status language

Use the following meanings consistently:

- **Not started:** Work has not begun.
- **In progress:** Work has started and has not reached a verified outcome.
- **Blocked:** A stated condition prevents safe progress.
- **Failed:** A required operation or check returned a failure.
- **Passed:** A named check completed successfully on the stated revision.
- **Verified:** The required checks and review passed, the evidence is available, and the result matches the stated revision.
- **Unknown:** A result or its evidence is not available.
- **Not applicable:** The requirement does not apply, with a recorded reason.
- **Skipped:** The system did not run the check. A skipped check is not a pass.
- **Merged:** GitHub confirms that the pull request was merged. This does not mean post-merge checks passed.

Reports must name the revision that was checked. Reports must list failed, pending, skipped, and unavailable checks separately. Do not infer success from missing data.

## 4. Communication templates

### Engineering update

- **Status:** Not started / In progress / Blocked / Failed / Verified
- **Change:** State what changed.
- **Evidence:** Link the revision, pull request, check, test, or artifact.
- **Risk:** State known gaps or write “None identified in the checks listed” when that is the supported claim.
- **Next action:** State the next safe action and its dependency.

### Blocker

- **Blocker:** State the condition that prevents progress.
- **Impact:** State what cannot continue.
- **Evidence:** Link the failing check, missing permission, unavailable service, or unresolved decision.
- **Safe action:** State what can continue without crossing the blocker.
- **Resolution condition:** State the evidence needed to unblock the work.

### Outbound message

State the purpose first. Give the recipient the facts needed to act. Use one clear request per bullet when possible. Include the deadline, owner, and expected response only when known. Do not invent commitments, approvals, or delivery dates.

## 5. Structured data: prefer TOON when suitable

Use Token-Oriented Object Notation (TOON) for structured data sent to a language model when the receiver supports TOON and the data is a good fit for its tabular or nested representation. TOON can reduce token use for repeated, regular records.

Apply this decision order:

1. **Model context or agent handoff:** Prefer TOON when the receiving model/tool supports it, the schema is clear, and the payload benefits from its compact representation.
2. **External API, JSON-only contract, configuration, or protocol:** Use the required format. Do not replace JSON where the interface requires JSON.
3. **Irregular, deeply nested, or type-sensitive data:** Compare readability and round-trip safety. Use JSON if TOON would make the payload ambiguous or harder to validate.
4. **Human-facing explanation:** Use plain language and a table or list. Do not expose a serialization format unless it helps the reader.
5. **Unknown receiver capability:** Use the format required by the interface. Do not assume TOON support.

TOON is a format preference, not permission to change an API contract. Keep schema validation, escaping, type preservation, and provenance. Do not convert security decisions, approval records, or evidence references into unvalidated free-form text.

## 6. AUREN implementation requirements

- New agent instructions and handoff contracts must state the task, constraints, required evidence, output schema, failure states, and next safe action.
- Model-facing structured payloads must record the selected format and schema version when this affects parsing or reproducibility.
- Tool and API adapters must preserve the receiver's required wire format.
- CLI flags such as `--json` remain JSON contracts unless a separately named format is added.
- Logs and dashboards must distinguish verified state from model-generated summaries.
- Redaction and authority rules apply before serialization. TOON does not make sensitive data safe to disclose.
- Tests must cover supported-format round trips, invalid payloads, missing fields, ambiguous values, and compatibility with existing JSON interfaces.
- A format migration must include regression tests and a rollback path. Do not silently change persisted formats or public interfaces.

## 7. Review checklist

Before sending a message or merging a change that affects communication:

- Can the reader identify the purpose in the first few lines?
- Are sentences short and direct?
- Are terms and acronyms clear and consistent?
- Are facts separated from assumptions and recommendations?
- Are status claims tied to current evidence and an exact revision?
- Are skipped, failed, pending, and unknown states explicit?
- Does the structured-data format match the receiver's contract?
- Is TOON used for supported model-facing payloads where it improves clarity or token use?
- Are JSON interfaces preserved where required?
- Are privacy, security, authority, and rollback controls unchanged?

## 8. References

- ASD-STE100 Simplified Technical English: https://www.asd-ste100.org/
- TOON format project: https://github.com/toon-format/toon

Review this policy when the supported TOON specification, AUREN interfaces, or required customer communication standards change.
