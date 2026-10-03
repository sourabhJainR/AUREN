# AUREN Agency Agent Quality Contract

AUREN treats specialist agents as production work units, not prompt snippets.

## What this adds from agency-agents

The upstream model is strong because each specialist has a clear identity, mission, rules, deliverables, workflow, communication style, learning behavior and measurable success criteria. AUREN adopts that structure while adding repository evidence, bounded execution, deterministic verification, security policy, impact analysis and an explicit output-quality gate. The upstream repository organizes specialists across 18 divisions and provides conversion and installation tooling for multiple agent runtimes.

## Agent contract

Every specialist used by AUREN should define:

1. Identity and operating perspective
2. Core mission and concrete outcomes
3. Critical rules and non-negotiable boundaries
4. Technical deliverables and reusable templates
5. Step-by-step workflow
6. Communication style
7. Success metrics and acceptance signals
8. Advanced capabilities and known failure modes
9. Required evidence and verification method
10. Escalation conditions and stop conditions

An agent is not allowed to claim success merely because it produced text or changed a file.

## Quality pipeline

```text
TASK
  -> classify
  -> select specialist(s)
  -> collect repository/domain evidence
  -> define acceptance contract
  -> plan deliverables
  -> execute bounded work
  -> verify artifacts
  -> independent review
  -> repair only material findings
  -> re-verify
  -> quality gate
  -> final report
```

## Specialist selection

Selection must consider:

- task intent and domain
- required artifact type
- repository technology
- risk and blast radius
- evidence requirements
- available provider/tool capabilities
- whether a specialist is primary, supporting, reviewer or verifier

Use one primary owner whenever possible. Add supporting specialists only when their independent perspective can change the result.

## Output standard

For any substantial deliverable, require:

- clear objective and audience
- assumptions separated from facts
- source/evidence traceability where applicable
- concrete implementation or decision output
- edge cases and failure modes
- verification results
- unresolved risks
- exact next action when work is incomplete

For code, also require tests, static checks, compatibility review and diff/scope review appropriate to the change.

For research, distinguish facts, inference, uncertainty and recommendation.

For design, distinguish requirements, design decisions, accessibility, responsive behavior and visual verification.

For business or analytical work, expose inputs, calculation basis, scenario boundaries and confidence limits.

## Review model

Use independent review for consequential work. Prefer reviewers with a different failure mode from the builder: security for security-sensitive changes, architecture for boundary changes, test/verification for behavioral changes, and domain specialists for domain claims.

Review findings must be classified as `blocker`, `material`, `minor`, or `observation`. Only blockers and material findings require repair before a pristine-success claim.

## Anti-patterns

- generic expert personas with no deliverables
- invented expertise or unsupported claims
- long process descriptions without decision points
- parallel edits to the same artifact
- using every available specialist for a simple task
- reporting "done" without evidence
- hiding uncertainty inside confident prose
- changing unrelated files for polish
- treating a delegated agent response as verification

## Upstream relationship

AUREN does not copy upstream behavior blindly. The upstream source is an agent-library source. AUREN owns the execution contract, safety, evidence, verification, state ledger, provider routing and promotion rules.
