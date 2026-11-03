# AUREN Engineering Execution Philosophy

This document defines operating rules for AUREN's planner, supervisor, agent crews, verifier and release path. It adapts proven engineering and management practices into enforceable runtime behavior. It is not permission to bypass Guard, approvals, independent evaluation or operator controls.

## 1. Eat the frog: remove the highest-cost blocker first

The planner assigns each task an explicit priority:

- **P0 — critical blocker:** security or authority boundary, data-loss risk, unknown side effect, broken recovery, release blocker, or a dependency that prevents safe progress.
- **P1 — important:** required capability or integration that materially advances an acceptance outcome.
- **P2 — useful:** quality, usability, maintainability or observability improvements that do not block safe delivery.
- **P3 — optional:** polish and speculative work.

Within the currently unblocked task set, AUREN selects P0 before P1, then P2, then P3. Within the same priority, it selects higher-risk work first. Original plan order is the deterministic tie-break. Dependencies are never bypassed to improve a score.

A P0 task must state the failure it prevents, the evidence needed to close it, its accountable owner and manager, and its rollback or containment path. A task cannot be promoted by a model-generated urgency claim alone; priority is pinned in the plan digest.

## 2. Fail fast, fail closed, and preserve useful work

- Validate inputs, identity, policy, scope, dependencies, budgets and deadlines before queue insertion or external launch.
- Stop the affected branch as soon as a required assumption, approval, test, provider outcome or trust boundary is invalid or unknown.
- Continue only independent work that cannot invalidate the blocked decision.
- Never convert missing, skipped, stale, timed-out or unscored evidence into a pass.
- Never retry an unchanged failure without new evidence or a changed, reviewed hypothesis.
- Bound retries, total time, fan-out, output size and cost. Escalate when the bound is reached.
- Treat an ambiguous external side effect as unknown, not failed. Reconcile it before retrying.

## 3. Attention to detail: make correctness observable

Every task needs:
- one accountable owner and one authorized reporting manager;
- a stable work key and immutable plan/task revision;
- precise scope, action class, policy and approval bindings;
- verifiable acceptance criteria and negative tests;
- a bounded budget and deadline;
- evidence references and a rollback/containment plan.

Before completion, verify edge cases, empty inputs, malformed state, duplicate submissions, concurrent execution, restart, cancellation, stale policy, partial writes, timeouts and unknown provider outcomes where applicable.

## 4. Small batches and short feedback loops

Prefer the smallest change that closes one acceptance gap. Compile early, run focused tests first, then broader regression and exact-head CI. Keep changes reviewable and independently reversible. Avoid mixing unrelated refactors with trust-boundary fixes.

A failed focused test blocks expansion of the change. Fix the cause or revert the slice before adding more work.

## 5. Independent review and separation of duties

Planning, implementation and post-implementation review use distinct responsibilities. The implementer cannot approve its own consequential work. Security and QA can block or request rework. Independent evaluation and sealed holdout custody must remain outside the candidate implementation trust domain. Different agent IDs alone do not prove independence.

Executive roles can prioritize and request evidence; they cannot waive mandatory policy, approval, CI, security, evaluation or release gates.

## 6. Limit work in progress and coordinate crews

Use the canonical supervisor, shared budget and durable task ledger. Do not create a parallel scheduler or duplicate source of truth. Start parallel work only when tasks have disjoint write scopes or an explicit conflict-resolution plan. Bound active and pending assignments per manager. Integrate frequently and rebase against the latest verified base before merging.

## 7. Learn from evidence, not activity

Record the hypothesis, baseline, change, test result, failure mode, cost and outcome for each meaningful attempt. Promote a technique into shared memory only after review, reproducibility and transfer checks. Keep failed, inconclusive, skipped and unknown outcomes visible. Do not reward task count, token volume or speed when acceptance quality falls.

## 8. Release only on verified evidence

A task is complete only when its acceptance evidence is present and current. A PR is mergeable only when required exact-head checks are terminal and allowed, required review is complete, the base is current, and no unresolved blocker remains. After merge, verify the exact main SHA and post-merge evidence. External isolation, evaluator custody, live provider behavior and operator rollback require their own evidence; repository CI cannot stand in for them.

## Current enforcement and remaining work

The canonical engineering plan now supports digest-bound P0–P3 priority and deterministic priority/risk ordering among ready tasks. The run-level ready-task projection excludes failed, blocked, running, verifying, passed and cancelled tasks, while allowing independent pending work to continue. Dependencies unlock only after a passed prerequisite. The gate-plan compiler marks core trust, rollback and release gates P0.

Conflict-scoped continuation is now implemented in the standalone graph orchestrator: every unresolved contradiction must map to one or more known graph nodes, and a trusted host-injected `conflict_scope_verifier` must approve that mapping against the reviewed contradiction set and graph digest. The mapped nodes are blocked, their dependents are skipped, and independent branches may continue. Missing, incomplete, invalid, or unverified mappings retain whole-graph fail-closed behavior. This is not yet proof that every production host composition root supplies a trustworthy verifier; that integration remains part of #1225.

Remaining work also includes broader planner-level semantic conflict detection, no-progress retry suppression, cross-root WIP admission, host isolation, independent evaluation custody, unknown-provider-outcome reconciliation and operator-controlled rollback rehearsal. Report each as open until its executable regression or external evidence exists.
