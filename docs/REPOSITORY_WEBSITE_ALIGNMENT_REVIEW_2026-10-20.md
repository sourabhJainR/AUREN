# AUREN Repository and Website Alignment Review

**Review date:** 2026-10-20  
**Current review baseline:** `main` at `2bba54f677d7d5b9cf38ea8916f5b999e5805544`  
**Current product version:** `1.0.5` (merged; current hard-stop fault-injection work is in a separate PR)  
**Scope:** repository-facing status, architecture claims, website/documentation consistency, workflow evidence, and implementation gaps.

## Executive summary

The current main revision contains durable supervisor-owned execution, revision-bound evidence receipts, canonical evidence-graph integration, fail-closed promotion checks, post-check revision revalidation, and capability-aware local-model ranking. Revision-specific AI Harness, runtime resilience, architecture integrity, hardening, portable bundle, agent capability, and Pages workflows passed for the reviewed commit. The bundle workflow produced portable and checksum artifacts.

Release 1.0.5 adds an explicit responsive-execution contract and a version-consistency regression test. Those results establish repository-level checks only. They do not establish independent external benchmark performance, OS-level sandbox isolation, production migration/rollback safety, or enforcement of GitHub required checks. The repository branch metadata returned `protected=true` but `protection.enabled=false`, enforcement off, and no required status checks. This is a high-priority governance gap.

## Canonical synchronization rules

1. `README.md` is the source-of-truth entrypoint for current product status and onboarding.
2. `docs/ARCHITECTURE_CURRENT.md` owns canonical domain responsibilities and runtime boundaries.
3. `site/index.html` is the published GitHub Pages source.
4. `docs/index.html` currently duplicates the website entrypoint and is also checked by the Pages workflow; keep it byte-for-byte synchronized until that legacy mirror is intentionally removed from the workflow.
5. `.github/workflows/deploy-pages.yml` deploys `site/`, while validating both `site/` and `docs/` entrypoints. After a website PR merges, verify the Pages workflow for the merge commit and confirm the public page refreshes.
6. Never convert CI status, checked-in fixtures, a registry entry, or receipt presence into claims of independent evaluation or production control.
7. Keep release version claims synchronized across README, site, VERSION, portable bundle manifest, plugin metadata, and release notes. Do not call a candidate released until exact-head checks and post-merge artifacts are verified.

## Post-review finding: single Pages deployment authority

The initial repository review found two competing publishers: the canonical `Deploy AUREN documentation` workflow deploys `site/`, while the sample Jekyll workflow built the repository root and used the same Pages concurrency group. Both could run on a push to `main`; the last deployment could therefore overwrite the intended site with a different build. The remediation in the follow-up Pages fix makes the legacy workflow manual-only and non-deploying, adds a regression contract, and ensures changes to the legacy workflow trigger the canonical publisher. After that fix merges, verify the canonical Pages workflow for the merge SHA and inspect the deployed content.

## Confirmed baseline evidence

| Area | Observed evidence | Limit |
|---|---|---|
| Main revision | `7e06b4c3abc7f1d5116eae22206ebec0ca11d4b3` | Recheck after every merge |
| AI Harness Check | [Run 39415100754](https://github.com/sourabhJainR/AUREN/actions/runs/39415100754) passed | Repository tests for that SHA |
| Runtime Resilience Matrix | [Run 39415100797](https://github.com/sourabhJainR/AUREN/actions/runs/39415100797) passed across Linux/Windows and Python 3.11/3.12 | Does not cover every production host or external effect |
| Architecture Integrity | [Run 39415100737](https://github.com/sourabhJainR/AUREN/actions/runs/39415100737) passed | Structural/repository contract only |
| Production AI Harness Hardening | [Run 39415100764](https://github.com/sourabhJainR/AUREN/actions/runs/39415100764) passed | Does not replace independent host isolation audit |
| Agent Capability Runtime | [Run 39415100723](https://github.com/sourabhJainR/AUREN/actions/runs/39415100723) passed | Revision-specific |
| Portable bundle | [Run 39415100744](https://github.com/sourabhJainR/AUREN/actions/runs/39415100744) passed and emitted `auren-portable` and `auren-portable-checksum` | GitHub artifact digests are distinct from the ZIP file's checksum |
| Pages deployment | [Run 39415100782](https://github.com/sourabhJainR/AUREN/actions/runs/39415100782) passed | Must be rerun/verified for this website update |

## Responsiveness and non-hanging execution

See [Responsive Execution and Timeout Policy](RESPONSIVE_EXECUTION_AND_TIMEOUTS.md). PR #743 added `portable.bounded_subprocess.run_bounded_command` plus fault-injection tests for hung commands, inherited parent deadlines, cancellation, bounded output, process-tree termination, pre-cancellation, and nonzero exits. This follow-up integrates it into the default revision-bound evidence gate and propagates the total deadline; it still does not wrap every provider, graph node, GitHub adapter, or validation path. Remaining risks include non-cooperative Python threads, providers that ignore cancellation, queue/lock contention, and ambiguous external effects. Hard-stop guarantees remain pending until adapters adopt the primitive and end-to-end acceptance tests cover those boundaries.

## Prioritized implementation gaps and enhancements

### P0 — Governance and fail-closed trust boundaries

- [ ] **Enforce main-branch required checks.** An owner/admin must configure GitHub branch protection/rulesets, require the appropriate exact-head CI checks, block force pushes and bypasses as appropriate, then verify effective rules via GitHub API. A green workflow is not branch protection.
- [ ] **Independent Arena evaluator and sealed corpus.** Establish separate custody for generator, oracle, holdout, signing keys and campaign runner; publish signed run receipts and fresh-holdout evidence. Ensure the runtime under test cannot read or modify sealed evaluation assets.
- [ ] **Actual sandbox/host isolation audit.** Independently test filesystem escape, secret exfiltration, network egress, process escape, resource exhaustion, and evaluator/receipt tampering. Some authority binding adapters are opt-in; inventory coverage is not proof all consequential call sites are enforced.
- [ ] **Consequential side-effect safety.** Inventory every external effect and enforce task/attempt idempotency keys, durable operation receipts, timeout ambiguity reconciliation, and compensating/rollback paths. Lease fencing prevents stale commits but cannot undo an already executed external effect.
- [ ] **Operator migration and rollback rehearsal.** Test clean install, legacy-state migration, conflicting state, interruption during migration, rollback, and recovery from partial rollback on a staging installation. Preserve legacy state and capture operator evidence before closing issue #534.

### P1 — Recovery and durable execution

- [ ] Add process-kill fault injection at each durable transition: task accepted, claim persisted, checkpoint written, external effect started, receipt committed, graph finalized, and evidence-graph edge persisted.
- [ ] Verify restart/resume is deterministic when execution ledger, checkpoint, recovery ledger and evidence graph disagree; fail closed or reconcile explicitly, never fabricate success.
- [ ] Test cancellation and lease expiry while a handler is still running; ensure stale workers cannot finalize and in-flight external effects are reconciled.
- [ ] Assert retry budgets are monotonic across restart and hypothesis changes; no repeated attempts without measurable progress; bounded time/cost and explicit terminal failure.
- [ ] Test handler source digest changes, durable schema migration, backup/restore, and operator recovery of pre-digest registrations.

### P1 — Evidence graph, learning and promotion

- [ ] Extend end-to-end tests from evidence receipt to canonical graph to supervisor acceptance to promotion decision; include missing, duplicate, stale, corrupt, mismatched revision, mismatched task/payload, failed command, and post-check revision changes.
- [ ] Verify graph writes are transactional/idempotent under duplicate receipt delivery and partial graph-store failure.
- [ ] Ensure failed, cancelled, stale, unscored or untrusted runs cannot enter positive memory or update model/strategy rankings.
- [ ] Track provenance for every learned claim: source identity, revision, task/attempt, evidence digest, evaluator identity, and retention/deletion policy.
- [ ] Make promotion/rollback decision records replayable and show why each gate passed or failed.

### P1 — Website and documentation correctness

- [ ] After merge, verify the deployed site serves the same latest content as `site/index.html`; record deployed commit and deployment manifest.
- [ ] Add a CI check that `site/index.html` and `docs/index.html` are byte-identical while both remain required by the Pages workflow.
- [ ] Add automated link/anchor checks for internal documentation links and validate that PR/issue status claims do not point to closed implementation issues as if they were open work.
- [ ] Generate the website's "latest implementation" section from a canonical status/evidence manifest where practical, avoiding hand-maintained PR summaries drifting from README.
- [ ] Clarify which entrypoint is canonical (`site/`) and either retain `docs/index.html` as a tested mirror or remove it from the Pages workflow intentionally in a separate reviewed cleanup.
- [ ] Keep version and release claims synchronized across README, site, `VERSION`, portable bundle manifest, plugin metadata, and release notes.

### P2 — Dashboard, multi-session and model routing

- [ ] Ensure dashboard session status is derived from durable run state, not a UI-local state or model-generated summary; expose session ID, stage, current attempt, timestamps, heartbeat/lease, cancellation, and terminal reason.
- [ ] Tie displayed learnings to canonical evidence graph node/receipt IDs and source revisions, with visible provenance and trust status.
- [ ] Add real multi-session concurrency, reconnect, restart, stale-update, and permission-isolation tests for AUREN/Muxin live sessions.
- [ ] Evaluate model routing with task-quality outcomes, privacy policy, latency, cost, resource pressure, and fallback behavior—not capability labels or latency alone. Keep private/restricted tasks fail-closed on remote fallback.
- [ ] Report provider discovery failures and route decisions as auditable evidence; avoid treating configured capabilities as verified competence without task outcomes.

### P2 — CI efficiency and release evidence

- [ ] Keep slow test groups sharded by measured duration and failure isolation; preserve exact-head required checks and publish per-shard timing/flake trends.
- [ ] Ensure impact-based skipping cannot skip security/trust-boundary checks when dependency or workflow changes affect those paths.
- [ ] Tie portable bundle, checksum, Pages manifest, test receipts, and release provenance to the same reviewed source SHA.
- [ ] Verify the ZIP's internal manifest and actual checksum separately from GitHub's artifact upload digest.

## Website claim policy

- Use **Passed** only for a workflow run with a matching reviewed SHA and successful conclusion.
- Use **Produced** for artifacts; do not imply the artifact is a signed production release unless signing/provenance is verified.
- Use **Pending** for independent external evaluation, sealed-corpus custody, external isolation audit, and operator rollback rehearsal until receipts exist.
- Clearly qualify checked-in transfer scores as fixtures, not real-world comparative performance.
- State that model/agent adapters propose work; supervisor policy and guarded gates retain execution and promotion authority.

## Acceptance criteria for closing this review

- [ ] README, site and docs entrypoint report the same review date, main SHA, product version, workflow evidence, and unresolved gates.
- [ ] `site/index.html` and `docs/index.html` are byte-identical, or the duplicate is removed from all validation/deployment contracts.
- [ ] All required checks pass on the exact PR head; merge only after required checks pass.
- [ ] After merge, main SHA is verified and Pages deployment succeeds for that SHA; deployed site is inspected.
- [ ] External/operational gates remain visibly pending until independently sourced evidence is recorded.
