# Quality Governance

The harness itself is a production system and must be reviewed like one.

## Continuous quality loop

```text
measure -> detect -> hypothesize -> change -> verify -> compare -> promote or revert
```

Review the harness across:

- routing accuracy
- problem-solving framework selection quality
- verification escape rate
- repair success rate
- unnecessary capability activation
- context/tool waste
- provider failure rate
- reviewer false-positive/false-negative patterns
- memory promotion quality
- architecture/naming drift
- security and permission drift

## Problem-solving quality

AUREN must use problem-solving frameworks as adaptive reasoning tools, not as mandatory ceremonies.

For non-trivial work, review whether:

- the problem was classified before action;
- the selected framework matched the work type and risk;
- evidence supported important findings;
- RCA reached an actionable cause where applicable;
- measurable work had a baseline and post-change comparison;
- consequential changes received a pre-mortem;
- material alternatives and tradeoffs were considered;
- uncertainty was represented without invented precision;
- the framework changed or validated the outcome rather than adding ceremony.

Representative evaluation cases should cover research, POC, development, maintenance, bugs, defects, incidents, architecture, review and self-improvement.

## Golden invariants

1. No unverified success.
2. No hidden third-party dependency.
3. No silent security-policy change.
4. No blind retry.
5. No parallel conflicting mutation.
6. No unnecessary context expansion.
7. No invented repository convention.
8. No placement decision without repository evidence when new files are added.
9. No permanent learning from one observation.
10. No external irreversible action without explicit policy approval.
11. No framework selection may bypass repository rules, acceptance criteria, security controls or human approval.
12. No framework may manufacture evidence, causal certainty or probability.

## Entropy control

Periodically scan for duplicated guidance, stale rules, unused capabilities, obsolete provider configuration, dead scripts, weak tests, and contradictory instructions.

Prefer small corrective changes over periodic rewrites.

## Promotion rule

A harness improvement is promoted only when its value is demonstrated through regression tests, representative evaluation cases, or operational evidence.

A problem-solving improvement must demonstrate better decision quality, verification quality, efficiency or risk control against a known baseline while preserving protected behavior.
