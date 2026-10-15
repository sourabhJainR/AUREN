# AUREN Engineering Reliability Pilot

## Who this is for

Engineering teams maintaining a medium-to-large codebase that need to reduce time spent understanding unfamiliar repositories, planning bounded changes, validating changes, and assembling regression/evidence trails. The pilot is not a general-purpose autonomous software engineer deployment.

## Pilot outcome

In a customer-approved repository, compare the team's existing workflow with an AUREN-assisted workflow on a fixed set of representative engineering tasks. Measure outcomes rather than model output volume.

**Primary question:** Does AUREN improve engineering throughput or verification quality without increasing escaped defects, unauthorized actions, or operational risk?

## Proposed scope

- One repository and one engineering team.
- One agreed task family, such as bug fixes, safe refactors, test-gap closure, or dependency-impact analysis.
- A baseline set of 8–15 tasks and a matched AUREN-assisted set where feasible.
- Existing CI and review requirements remain authoritative.
- Every task records the starting revision, task brief, acceptance criteria, changes, tests, review effort, regressions, and final revision.
- A readout that includes successful and failed tasks, resource/cost estimates, limitations, and a recommendation to stop, extend, or expand.

Exclude production deployment, privileged credential access, and irreversible external effects unless separately approved and explicitly tested.

## Suggested delivery plan

1. **Discovery and scope (days 1–3):** agree task family, data boundary, baseline workflow, metrics, access and rollback.
2. **Baseline capture (days 3–5):** record task lead time, review time, changed-path test coverage, regressions and evidence completeness.
3. **Controlled pilot (week 2):** run matched tasks with a human reviewer and the customer's existing CI gates.
4. **Readout (end of week 2):** compare results, disclose failures and uncertainty, recommend next step.

Schedule and task count should be agreed in writing; the dates above are a starting template, not a delivery guarantee.

## Acceptance measures

Agree definitions and collection methods before starting. Suggested measures:

- Median task lead time, from accepted task to reviewed change.
- Human review time per task.
- Escaped regressions and rollback events.
- Changed-path test coverage or agreed verification coverage.
- Evidence completeness: percentage of tasks with linked task, revision, checks, review and outcome.
- Unauthorized or out-of-scope actions (target: zero).
- Recovery rate after injected or naturally occurring transient failures.
- Compute/provider cost and operator intervention time.

Do not claim causal improvement from a tiny or unmatched sample. Report sample sizes, missing data, task difficulty, uncertainty and counterexamples.

## Security and data boundary

Before access is granted, complete the security/data-flow worksheet in [SECURITY_DATA_FLOW_TEMPLATE.md](SECURITY_DATA_FLOW_TEMPLATE.md). Identify each model/provider call, network destination, secret source, workspace location, logs, evidence store, retention period, and human approval boundary. Prefer least-privilege credentials, a curated workspace and isolated execution where supported. The customer owns repository access and may stop the pilot at any time.

Do not describe a run as independently evaluated or independently isolated unless an independent operator actually performs and records that work. Repository CI alone is not an independent audit.

## Commercial hypothesis

Issue #517 proposes testing a fixed-scope pilot price hypothesis of **₹1.5–5 lakh**, adjusted to scope and support. This is a hypothesis to validate with buyers, not a market benchmark or an established price. Final scope, price, responsibilities, taxes and acceptance terms require a mutually agreed statement of work.

## Exit decision

- **Expand:** measured benefit is credible, safety gates pass, and the customer wants to continue.
- **Repeat with changes:** results are inconclusive or task selection/data quality need correction.
- **Stop:** unacceptable data-flow risk, unauthorized actions, uncontained regressions, or no evidence of useful benefit.

The pilot does not promise a production SLA, autonomous merge/release, a specific productivity gain, or independent-generalization proof.
