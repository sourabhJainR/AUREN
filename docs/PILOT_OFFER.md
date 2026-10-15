# AUREN engineering pilot — fixed-scope offer

## Who this is for

Engineering/platform teams maintaining a large or fast-changing codebase where repository understanding, implementation planning, regression verification, and evidence capture consume substantial senior-engineer time.

## Pilot objective

Test whether AUREN improves a customer's agreed engineering workflow on representative, customer-approved tasks. The pilot is an evaluation, not a promise of autonomous delivery or production safety.

## Proposed scope (2–3 weeks)

1. Select one repository or isolated representative project and 10–20 agreed tasks.
2. Record a baseline using the customer's existing workflow before running AUREN.
3. Run the same tasks through the AUREN workflow with human approval retained for consequential changes.
4. Review accepted changes, tests, regressions, rework, review effort, tool/model cost, latency, and evidence completeness.
5. Demonstrate failure handling and rollback on a non-production branch or disposable environment.
6. Deliver a findings report with raw measurement definitions, limitations, security/data-flow notes, and a continue/stop recommendation.

## Acceptance criteria (agree before the pilot)

- Task set, exclusions, and success rules frozen before measurement.
- Same tasks and equivalent acceptance tests used for baseline and AUREN runs.
- Every result classified as accepted, rejected, incomplete, or invalidated by environment/tooling.
- Human review and rollback remain available; no production writes without explicit customer authorization.
- Security/data-flow and retention configuration reviewed before customer code is supplied.
- Findings include negative results and uncertainty; no benchmark or productivity claim is generalized beyond the sample.

## Commercial hypothesis

The initial price range of ₹1.5–5 lakh per pilot is a hypothesis to test with buyers, not an established market price. Final price depends on repository access, provider costs, task count, data controls, and support effort. Do not quote a fixed offer until the customer-specific statement of work and security review are agreed.

## Customer responsibilities

- Provide a technical sponsor, task owner, baseline workflow, and acceptance reviewers.
- Approve repository access, provider/data-flow configuration, and retention policy.
- Identify excluded paths, secrets, regulated data, and non-production boundaries.
- Attend kickoff, midpoint review, and final readout.

## Explicit exclusions

- Guaranteed productivity gains, zero defects, or autonomous production operation.
- Independent Arena certification or generalization claims.
- SSO/RBAC, private hosted control plane, SLA, custom integrations, or compliance certifications unless separately implemented and verified.
- Use of customer code, telemetry, or results for training or public case studies without explicit written permission.

## Pilot decision

At the readout, choose one: stop; repeat with a revised hypothesis; or propose a separately scoped deployment. A positive result requires measurable improvement against the pre-agreed baseline without unacceptable regression, security, cost, or review trade-offs.
