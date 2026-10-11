# Task Trust and Option Selection Policy

## Purpose

This policy makes engineering choices explainable and makes trust something a system earns through observed outcomes, not confidence, verbosity, familiarity, or repeated assertions. It applies to repository work, maintenance, research, implementation, and autonomous task execution.

This policy is repository-local. It does not add runtime dependencies on another repository, and it does not transfer one repository's verification results or trust state to another.

## Non-negotiable rules

1. **Repository-local truth:** inspect the current repository, its instructions, branch, status, toolchain, tests, and actual diff before selecting an implementation. Do not assume another project's conventions or capabilities apply.
2. **Evidence outranks confidence:** command output, test results, review findings, signed/immutable receipts, and observed outcomes outrank a model's summary.
3. **No claim inflation:** compilation is not correctness; unit tests are not full integration; repository-controlled evaluation is not independent evaluation; a benchmark without a comparable baseline is not proof of improvement.
4. **Trust is scoped:** record trust by capability/task family, repository snapshot, tool/provider path, and evidence freshness. Never carry a score, permission, or promotion from one repository or task family into another. Transfer only a general method or hypothesis; verify it locally.
5. **Fail closed on critical uncertainty:** missing authorization, security evidence, required checks, or a trustworthy oracle blocks the affected action. Unrelated safe work may continue.
6. **Bounded recovery:** every retry must add evidence, change the hypothesis, change the strategy, or address a new environmental condition. Stop repeated no-progress attempts and surface the blocker with alternatives.
7. **Reversible by default:** isolate changes, preserve the base commit and exact head SHA, minimize scope, define rollback before risky mutation, and never silently rewrite unrelated user work.
8. **Independent verification:** for material or risky changes, review the actual diff from a fresh context and use an independent oracle where feasible. Author self-report is not independent proof.
9. **Human/authority boundaries remain binding:** trust never grants new permissions, bypasses approvals, changes security policy, or authorizes irreversible external actions.

## Trust model: a vector, not a magic score

Maintain a separate status for each dimension instead of collapsing them into one opaque number:

| Dimension | Evidence that increases confidence | Evidence that decreases confidence |
|---|---|---|
| Task understanding | Acceptance criteria mapped to concrete checks; unresolved assumptions surfaced | Hidden assumptions, scope drift, repeated reinterpretation |
| Repository grounding | Current branch/commit inspected; local instructions and conventions followed | Stale context, wrong branch, edits based on another repository's structure |
| Correctness | Focused tests and relevant regression suite pass on the exact change | Failing tests, unexplained flaky tests, regression or untested critical paths |
| Review independence | Fresh-context review; findings tied to exact lines and behavior | Rubber-stamp review, author-only assertions, unresolved critical findings |
| Evidence integrity | Exact commit, command, exit status, environment, artifact digest, and provenance retained | Missing logs, mutable evidence, mismatched commit, fabricated or unverifiable result |
| Operational safety | Least privilege, isolation, resource limits, rollback and recovery tested | Permission escalation, unsafe side effects, no recovery route |
| Generalization | New tasks/domains and sealed holdouts pass under an independent evaluator | Repeated training examples only, leaked holdouts, evaluator/runtime control by same actor |
| Outcome reliability | Repeated successful outcomes in the same task family, with failures retained | Recurring failure, silent failure, selective reporting, stale evidence |

A successful run increases confidence only for the dimensions it actually tests. Do not let high correctness compensate for missing authorization or a critical security failure.

### Operational states

- **Unassessed:** insufficient repository-local evidence. Use conservative execution and deeper verification.
- **Provisional:** one successful, scoped observation. Useful for a hypothesis or low-risk trial; not enough for broad promotion.
- **Verified for scope:** acceptance checks passed on an identified repository snapshot and required review found no blocking issue.
- **Repeatedly reliable for scope:** multiple successful runs or tasks in the same task family, including regression checks and retained failure history.
- **Independently corroborated:** an evaluator/oracle outside the adaptive implementation boundary reproduced the result against an independently controlled corpus or acceptance contract.
- **Degraded / suspended:** regression, stale or contradictory evidence, integrity failure, or repeated repair failure. Reduce autonomy, increase verification, isolate, or roll back.

These are scoped states, not universal badges. A change to the repository snapshot, toolchain, provider, policy, evaluator, or task family may require re-verification. Independent corroboration is not a prerequisite for every low-risk task, but it is required before claiming independent generalization.

## Selecting among options

For each non-trivial task, compare at least two viable options when alternatives materially differ. Record why the selected option fits this task and why the nearest alternative was rejected. Do not manufacture alternatives for a trivial, obvious fix.

Evaluate each option against:

- acceptance-criteria coverage and expected user value;
- correctness/security/reliability risk and blast radius;
- reversibility and rollback cost;
- compatibility with repository architecture and public interfaces;
- implementation and maintenance cost;
- dependency, permission, network, and data exposure;
- verification quality and availability of an independent oracle;
- time-to-evidence and cost of a failed attempt;
- uncertainty, evidence freshness, and opportunity to learn.

Use hard constraints first: reject options that violate policy, permissions, required compatibility, or safety invariants. For remaining options, choose the smallest option that meets the acceptance criteria with adequate verification and the lowest reasonable total risk/cost. Do not use a numerical weighted score unless the weights and evidence are explicit and sensitivity to the weights is understood.

### Common trade-offs

| Decision | Prefer option A when | Choose option B when | Principal trade-off |
|---|---|---|---|
| Minimal patch vs. broad refactor | A scoped defect has a local, testable fix | Evidence shows the defect is caused by a structural constraint or repeated systemic failures | Minimal patches reduce blast radius; refactors can remove root causes but multiply regression surface |
| Existing dependency vs. new dependency | Existing code covers the need and is maintained | A new dependency provides a material capability not reasonably implemented locally and passes license/security/maintenance review | Reuse lowers supply-chain and operational cost; a new dependency may improve capability but adds updates, attack surface, and failure modes |
| Sequential vs. parallel work | Tasks share files/state, dependencies are unclear, or failures are hard to attribute | Tasks are independent, isolated, and have an explicit synthesis/merge step | Parallelism improves latency but increases resource use, conflict risk, and integration work |
| Local vs. external execution | Data is sensitive, network access is unnecessary, or reproducibility matters | External resources are necessary, authorized, and offer measurable capability or scale | Local execution improves data control; external execution may improve capability but adds privacy, availability, cost, and provenance risks |
| Focused tests vs. full suite | Fast diagnosis is needed after a localized change | Before merge/release, or when change has broad/shared impact | Focused tests shorten feedback; the full suite catches cross-component regressions at higher cost |
| Same-context vs. independent review | Change is trivial and low risk | Change is material, security-sensitive, architectural, or difficult to falsify | Same-context review is cheaper; independent review reduces confirmation bias and catches missing assumptions |
| Auto-merge vs. gated merge | Only when explicit repository policy allows it and all exact-head required checks, review, and rollback gates pass | When required checks are missing, branch protection or authority is unclear, or change is high impact | Automation reduces idle time; gating prevents unverified promotion and unauthorized changes |
| Retry vs. re-plan | A transient/environmental fault is evidenced and retry is bounded | The same hypothesis failed, evidence contradicts the plan, or progress stalled | Retry is cheap for transient failures; re-planning avoids loops and repeated damage |
| Internal benchmark vs. independent evaluation | Internal benchmark is suitable for regression and iteration | A public capability/generalization claim or high-stakes promotion requires independent control | Internal tests are cheap and repeatable; independent evaluation costs more and needs corpus/oracle/key separation but reduces self-evaluation bias |

### Risk-proportionate default

- **Low risk** (documentation, isolated formatting, local non-functional change): inspect relevant files, make a small diff, run focused checks, and review the diff.
- **Medium risk** (behavioral fix, public interface, shared runtime): write acceptance criteria, compare options, add regression coverage, run focused plus integration checks, and obtain a fresh-context review.
- **High risk** (security, authority, persistence/schema, release, broad refactor, external side effects): plan first; use isolation and least privilege; record a pre-mortem and rollback; run full required checks; require independent review and policy-authorized approval; canary where appropriate.
- **Capability or generalization claim:** preserve sealed/held-out inputs, separate evaluator authority, bind evidence to immutable identities, repeat runs, and disclose what the evaluation does not establish.

## Evidence record for a consequential choice

Record a compact decision entry alongside the task report:

- task ID, repository identity, base and head commit;
- acceptance criteria and non-goals;
- viable options and the reason each was accepted/rejected;
- selected option and expected benefit;
- material risks, assumptions, dependencies, and reversibility;
- tests/evaluator, exact commands, exit status, and evidence/artifact digests;
- review findings and their disposition;
- unresolved limitations and next decision threshold;
- rollback trigger, action, and owner/authority;
- final observed outcome, including failures and recovery attempts.

Do not store secrets or sensitive data in this record. Link to the canonical evidence store rather than creating duplicate sources of truth.

## Promotion, degradation, and rollback

Promote a recommendation only when the evidence matches the current task family and snapshot, is sufficiently fresh, and meets the applicable verification bar. One successful run can validate a narrow change; it cannot establish broad reliability.

Degrade or suspend a route/capability when a critical check fails, a regression appears, evidence integrity is doubtful, or repeated attempts make no progress. Stop new mutations, preserve the failing evidence, isolate the change, select a different strategy, and roll back when the rollback trigger is met. Restore confidence only after a successful recovery plus the relevant regression checks; do not erase the failure from the record.

## Honest reporting contract

Every completion report must separate:

1. **Changed:** what actually changed and at which commit.
2. **Proven:** which checks passed on that exact commit.
3. **Observed:** measurements and outcome evidence, with baseline and environment when comparing.
4. **Not proven:** missing checks, independent evaluation, performance causality, or generalization.
5. **Decision:** why the chosen option beat the nearest alternative for this task.
6. **Recovery:** known failure modes, rollback route, and remaining risks.

A CI pass proves that the recorded checks passed for that revision; it does not by itself prove production correctness, independent generalization, or that a performance optimization caused a speedup.
