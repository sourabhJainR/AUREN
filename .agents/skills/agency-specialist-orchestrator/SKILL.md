---
name: agency-specialist-orchestrator
description: Route work through specialist agents using evidence, explicit deliverables, bounded execution and independent quality review.
---

# Agency Specialist Orchestrator

Use this skill when a task benefits from domain specialization, multiple viewpoints, or a polished deliverable.

## Operating rule

Treat the upstream agency library as a specialist knowledge base. Treat AUREN as the control plane. A specialist can recommend, create or review artifacts, but AUREN acceptance, security, verification and promotion rules remain authoritative.

## Route

1. Classify the task: engineering, design, research, product, business, security, testing, operations or specialist domain.
2. Identify the primary deliverable and its audience.
3. Inspect repository instructions, current state and available evidence before selecting agents.
4. Select one primary specialist. Add supporting specialists only for independent expertise or a material risk.
5. Give each specialist a bounded role, inputs, constraints, expected artifacts and acceptance criteria.
6. Keep read-only investigation parallel where useful; serialize conflicting mutations.
7. Require evidence for material claims and verification for material artifacts.
8. Run an independent reviewer with a different failure mode from the builder.
9. Repair only blockers/material findings, then re-run the affected verification.
10. Apply the final OutputQualityGate before claiming ready.

## Specialist brief

```text
ROLE:
TASK:
CONTEXT:
KNOWN FACTS:
UNKNOWN / ASSUMPTIONS:
NON-GOALS:
INPUTS:
EXPECTED DELIVERABLES:
ACCEPTANCE:
EVIDENCE REQUIRED:
VERIFICATION:
STOP / ESCALATE IF:
```

## Output contract

A polished result must contain, as applicable:

- final artifact, not only discussion
- facts vs assumptions
- decisions and rationale
- evidence/source traceability
- edge cases and failure modes
- verification performed
- review findings and disposition
- open risks and limitations
- exact next steps if incomplete

Never hide uncertainty to make an answer sound complete.

## Quality bar

Reject output that is generic, unsupported, internally inconsistent, outside scope, unverified, or missing the requested deliverable. Prefer a smaller verified result over a broad unverified result.

## Upstream sync

Use `python scripts/sync_agency_agents.py --ref <commit>` to vendor the pinned specialist library. Run `python scripts/check_agency_agents.py` before committing a vendored snapshot.
