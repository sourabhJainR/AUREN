# AUREN Repository and Website Alignment Review

**Review date:** 2026-10-21  
**Latest verified main before this documentation PR:** `dc395dac3c832154769344f13006d39bc387e6b4`  
**Current product version:** `1.0.5` (merged)  
**Scope:** repository-facing status, architecture claims, website/documentation consistency, workflow evidence, and implementation gaps.

## Latest synchronization update — 2026-10-21

PR [#764](https://github.com/sourabhJainR/AUREN/pull/764) merged at [`dc395dac3c832154769344f13006d39bc387e6b4`](https://github.com/sourabhJainR/AUREN/commit/dc395dac3c832154769344f13006d39bc387e6b4), followed by documentation/Pages synchronization in PR [#765](https://github.com/sourabhJainR/AUREN/pull/765), merged at [`0fd4380ba78a3ad02ce8cdfe7b243f828c12e317`](https://github.com/sourabhJainR/AUREN/commit/0fd4380ba78a3ad02ce8cdfe7b243f828c12e317). PR #765 exact-head checks and post-merge workflows, including Pages deployment, passed. PR [#766](https://github.com/sourabhJainR/AUREN/pull/766) connected supervisor lifecycle events to the journal and dashboard. PR [#767](https://github.com/sourabhJainR/AUREN/pull/767) added the read-only CLI view and merged at `dbc9dab7798d413be67b7154ea7e07ff795ef373`; all nine post-merge workflows, including Pages deployment, passed. Provider-specific progress callbacks, cross-store crash reconciliation, and hard termination/deadline/cancellation fault injection remain open. The current product version remains `1.0.5`.

The merged progress journal provides durable append-only events, monotonic per-task sequencing, cross-instance/process writer locking, heartbeats, staleness reporting, terminal-state handling, redaction, and concurrent-writer tests. The current integration slice connects `ExecutionController` lifecycle transitions to the journal and projects snapshots into the dashboard API/console; provider-specific progress callbacks, cross-store crash reconciliation, and hard termination/deadline/cancellation fault injection remain open. CLI journal inspection is available through `python auren_cli.py progress --json`. Treat this update as a status synchronization, not closure of the outstanding runtime, trust, evaluation, migration, or external-operation gates.

## Executive summary

The current main revision contains the durable progress journal merged in PR #764, supervisor/dashboard integration in PR #766, and the read-only CLI view in PR #767, alongside the previously implemented supervisor-owned execution, revision-bound evidence receipts, canonical evidence-graph integration, fail-closed promotion checks, capability-aware local-model ranking, hard-stop subprocess execution, deadline propagation into selected evidence checks, opt-in process-isolated crew dispatch, and Windows Job Object descendant containment. Exact-head CI passed for each merged PR; PR #767's nine post-merge workflows and Pages deployment also passed on `dbc9dab7798d413be67b7154ea7e07ff795ef373`. Provider callbacks, cross-store crash recovery, and deadline/cancellation/hard-termination fault injection remain open.

Release 1.0.5 adds an explicit responsive-execution contract and a version-consistency regression test. PR #743 adds executable process-tree hard-stop tests; PR #747 integrates hard-stop execution and deadline propagation into the default revision-bound evidence gate; PR #749 adds opt-in process-isolated crew execution and fixes a concurrent dispatch replay row-mapping race; PR #753 adds Windows Job Object containment and a delayed-descendant side-effect test. These results establish repository-level checks only. They do not establish independent external benchmark performance, OS-level sandbox isolation, production migration/rollback safety, or enforcement of GitHub required checks. The repository branch metadata returned `protected=true` but `protection.enabled=false`, enforcement off, and no required status checks. This is a high-priority governance gap.

## Canonical synchronization rules

1. `README.md` is the source-of-truth entrypoint for current product status and onboarding.
2. `docs/ARCHITECTURE_CURRENT.md` owns canonical domain responsibilities and runtime boundaries.
3. `site/index.html` is the published GitHub Pages source.
4. `docs/index.html` currently duplicates the website entrypoint and is also checked by the Pages workflow; keep it byte-for-byte synchronized until that legacy mirror is intentionally removed from the workflow.
5. `.github/workflows/deploy-pages.yml` deploys `site/`, while validating both `site/` and `docs/` entrypoints. After a website PR merges, verify the Pages workflow for the merge commit and confirm the public page refreshes.
6. Never convert CI status, checked-in fixtures, a registry entry, or receipt presence into claims of independent evaluation or production control.
7. Keep release version claims synchronized across README, site, VERSION, portable bundle manifest, plugin metadata, and release notes. Do not call a candidate released until exact-head checks and post-merge artifacts are verified.

## Post-review finding: single Pages deployment authority

The initial repository review found two competing publishers: the canonical `Deploy AUREN documentation` workflow deploys `site/`, while the sample Jekyll workflow built the repository root and used the same Pages concurrency group. Both could run on a push to `main`; the last deployment could therefore overwrite the intended site with a different build. The remediation in the follow-up Pages fix makes the legacy workflow manual-only and non-deploying, adds a regression contract, and ensures changes to the legacy workflow trigger the canonical publisher. The legacy publisher remediation is present on the reviewed branch; this documentation PR adds an explicit byte-for-byte mirror check. After merge, verify the canonical Pages workflow for the merge SHA and inspect the deployed content.

## Historical evidence from the `fe7452118b95` baseline (not current-main evidence)

| Area | Historical evidence from the `fe7452118b95` baseline | Limit |
|---|---|---|
| Historical baseline | `fe7452118b95d110c1be48f2b7a731a7843e5ddd` | Historical only; latest main is `dc395dac3c832154769344f13006d39bc387e6b4` |
| AI Harness Check | [Run 39435832928](https://github.com/sourabhJainR/AUREN/actions/runs/39435832928) passed on `fe7452118b95` | Revision-specific repository checks only |
| Runtime Resilience Matrix | [Run 39435832872](https://github.com/sourabhJainR/AUREN/actions/runs/39435832872) passed on `fe7452118b95` across Linux/Windows and Python 3.11/3.12, including the checkpoint crash matrix | Check its jobs for impact-based skips; previous full Linux/Windows and Python 3.11/3.12 runtime evidence is revision-specific |
| Architecture Integrity | [Run 39435832873](https://github.com/sourabhJainR/AUREN/actions/runs/39435832873) passed on `fe7452118b95` | Structural/repository contract only |
| Production AI Harness Hardening | [Run 39435832845](https://github.com/sourabhJainR/AUREN/actions/runs/39435832845) passed on `fe7452118b95` | Does not replace independent host isolation audit |
| Agent Capability Runtime | [Run 39435833143](https://github.com/sourabhJainR/AUREN/actions/runs/39435833143) passed on `fe7452118b95` | Revision-specific |
| Portable bundle | [Run 39435832953](https://github.com/sourabhJainR/AUREN/actions/runs/39435832953) produced the portable bundle (1,169,853 bytes; artifact digest `sha256:aa0470d1ce397e1470f9d22196441d002f4bf33c09c9ad5e336b5c12a352af4b`) and checksum artifact (234 bytes; artifact digest `sha256:6be8a957c7660139ac16ec1e24b6dbd2a180657da592bc89de1313f268203923`) on `fe7452118b95` | Artifact content matches the last code-impact revision; no bundle was rebuilt for docs-only commits. Artifact digests are not the ZIP's internal SHA-256 |
| Pages deployment | [Run 39435833065](https://github.com/sourabhJainR/AUREN/actions/runs/39435833065) passed on `fe7452118b95`; emitted Pages and deployment-manifest artifacts | Confirms workflow success; public cache propagation is separate |

## Responsiveness and non-hanging execution

See [Responsive Execution and Timeout Policy](RESPONSIVE_EXECUTION_AND_TIMEOUTS.md). `portable.bounded_subprocess.run_bounded_command` is exercised against hangs, inherited deadlines, cancellation, output floods, and process-tree termination. The default `portable.evidence_gate.run_evidence_gate` path uses it for revision-bound checks. `portable.process_isolated_crew_dispatch.ProcessIsolatedCrewDispatcher` provides a deadline-bound process-isolation option for trusted importable crew handlers, while the legacy thread dispatcher remains non-killable. Remaining risks include arbitrary provider/network calls and graph nodes not yet routed through hard-stop adapters, Windows Job Object descendant containment is now covered by CI, queue/lock contention, and ambiguous external side effects. These features are liveness controls, not a sandbox or proof of production isolation. The latest main bundle workflow was impact-skipped for documentation-only changes; the latest code-impact bundle remains available from `071991f5941f`. Rebuild on code-impact or a controlled release dispatch when exact-SHA artifact provenance is required.

## Prioritized implementation gaps and enhancements

### P0 — Governance and fail-closed trust boundaries

- [ ] **Enforce main-branch required checks.** An owner/admin must configure GitHub branch protection/rulesets, require the appropriate exact-head CI checks, block force pushes and bypasses as appropriate, then verify effective rules via GitHub API. A green workflow is not branch protection.
- [ ] **Independent Arena evaluator and sealed corpus.** Establish separate custody for generator, oracle, holdout, signing keys and campaign runner; publish signed run receipts and fresh-holdout evidence. Ensure the runtime under test cannot read or modify sealed evaluation assets.
- [ ] **Actual sandbox/host isolation audit.** Independently test filesystem escape, secret exfiltration, network egress, process escape, resource exhaustion, and evaluator/receipt tampering. Some authority binding adapters are opt-in; inventory coverage is not proof all consequential call sites are enforced.
- [ ] **Consequential side-effect safety.** Inventory every external effect and enforce task/attempt idempotency keys, durable operation receipts, timeout ambiguity reconciliation, and compensating/rollback paths. Lease fencing prevents stale commits but cannot undo an already executed external effect.
- [ ] **Operator migration and rollback rehearsal.** Test clean install, legacy-state migration, conflicting state, interruption during migration, rollback, and recovery from partial rollback on a staging installation. Preserve legacy state and capture operator evidence before closing issue #534.

### P1 — Recovery and durable execution

- [ ] Add process-kill fault injection at each durable transition: task accepted, claim persisted, checkpoint written, external effect started, receipt committed, graph finalized, and evidence-graph edge persisted. PR #759 adds checkpoint-store crash and conflicting-identity cases. This PR adds side-effect process-crash coverage before and after provider acceptance; the full cross-store transition matrix remains open.
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

- [ ] After this documentation refresh merges, verify the deployed site serves the same content as `site/index.html`; record the deployed commit and deployment manifest.
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
