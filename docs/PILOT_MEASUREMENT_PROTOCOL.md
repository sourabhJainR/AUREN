# AUREN pilot measurement protocol

Use this protocol to prevent cherry-picking and to make the pilot repeatable. Complete the task manifest and thresholds with the customer before running either workflow.

## Freeze before execution

- Repository and commit(s), environment, tool/provider versions, policy/configuration digests.
- Task IDs, task-family labels, complexity bands, exclusions, acceptance tests, and baseline method.
- Human review rules, definition of accepted change, severity thresholds, and invalid-run rules.
- Maximum time, tool/model cost, and resource budgets.
- Data-flow, secret-handling, retention, and deletion commitments.

## Record per task

| Metric | Operational definition |
|---|---|
| Lead time | Elapsed time from task start to reviewer decision; report median and distribution |
| First-pass acceptance | Accepted on first review without code-changing rework |
| Escaped regressions | Defects attributable to the change found after acceptance, by severity |
| Review effort | Human active review minutes, excluding unrelated work |
| Rework | Number of substantive implementation/review cycles before acceptance |
| Changed-path test coverage | Relevant changed paths covered by executed tests, with method and limitations |
| Evidence completeness | Required plan, diff, test, review, and outcome artifacts present and linked |
| Tool/model cost | Provider/tool calls, tokens where available, and actual billed cost where available |
| Latency | Wall-clock and active execution time, separately |
| Task disposition | Accepted, rejected, incomplete, invalidated, or blocked, with reason |

## Comparison rules

- Compare identical task IDs and acceptance criteria wherever feasible.
- Report sample size, missing runs, invalidations, and the reason for every exclusion.
- Report median and range; do not report only the best task or average.
- Separate results by task family and complexity band.
- Treat regressions and security-policy violations as guardrails, not as costs that can be offset by speed.
- Report baseline and AUREN results side by side; no causal claim if environments or task mixes materially differ.
- Preserve raw sanitized records and the exact configuration/commit identifiers needed to reproduce the summary.

## Stop conditions

Stop the pilot and escalate if customer data leaves the approved boundary, credentials are exposed, a consequential action bypasses authorization, rollback is unavailable, or a critical regression is observed. Do not continue just to complete the sample.

## Report template

1. Scope, owners, dates, repository/configuration digests.
2. Frozen task manifest and exclusions.
3. Baseline and AUREN measurements by task and family.
4. Accepted changes, rejected attempts, regressions, and rework.
5. Security/data-flow findings and stop-condition events.
6. Cost, latency, uncertainty, and sample limitations.
7. Decision: stop, repeat, or separately scoped next phase.

This is a measurement template, not customer evidence. Publish no customer name, code, metrics, or case study without written permission.
