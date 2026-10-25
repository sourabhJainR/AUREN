# Standalone Readiness and Important-Gap Matrix

**Reviewed:** 2026-10-21 14:20 UTC  
**Product version:** 1.0.5 (no release created by this review)  
**Status vocabulary:** implemented locally, repository-verified, deployment-verified, externally verified, or open. A merged PR or green CI does not establish external isolation or independent evaluation.

## Product boundaries

| Product surface | Existing foundation | Current boundary | Exit gate |
|---|---|---|---|
| AUREN CLI and portable runtime | `auren_cli.py`, `portable/auren_runtime.py`, portable bundle builder, machine-scoped install/update/rollback | A portable bundle is not the same as a separately published, clean-machine-installable SDK package | Build from a pinned source revision; install into a clean environment without a source checkout; verify manifest/hash; execute smoke test; update and rollback with preserved user data |
| Local dashboard / Run Center | `portable/dashboard_server.py`, `portable/engineering_dashboard.py`, progress journal and plan/run store | Dashboard reads multiple durable sources; all operational controls and views must be checked against canonical supervisor state | API/UI state agrees with durable run records after restart, timeout, cancellation, provider loss, and partial writes; every action is authorized and returns a durable result |
| Engineering memory | Persistent memory, memory orchestrator, evidence/provenance modules | A common memory implementation exists, but cross-agent, cross-branch retrieval and provenance correctness require workload-level proof | Shared namespace and project/branch scope are explicit; source refs, freshness, contradiction handling, retraction, and access policy are tested; failed or unscored outcomes cannot become positive lessons |
| Extension kit | Versioned data-only extension manifest in `portable/extension_contract.py`; documented extension contract | Manifest validation describes an extension; it does not load code, grant authority, sandbox a process, or verify an external provider's claims | Versioned compatibility policy, permission review, bounded process/transport adapter, output limits, timeout/cancellation, upgrade/rollback and malicious-extension tests |
| Independent evaluation kit | Arena, benchmark, sealed-corpus/evaluator attestation contracts | Repository-side receipts do not prove evaluator independence, holdout custody, or a production sandbox | Separate evaluator and candidate execution; externally controlled sealed corpus and keys; signed lineage; access-control and leakage tests; independent audit |
| Hybrid model routing | Provider fabric, local-model discovery, hybrid router and bounded fallback | A detected model or a route decision is not proof of suitability, privacy, or available capacity | Capability and privacy policy, measured latency/resource data, bounded fallback, unavailable-local-model test, and redaction tests |

## Priority implementation gates

### P0 — Execution safety and truthful progress

- [x] Shared bounded provider-call wait with a global cap on hung provider workers.
- [x] Provider timeout progress events identify the task and block implicit retry while a late result remains possible.
- [x] Durable execution and progress-journal recovery have regression coverage for selected commit boundaries.
- [ ] Extend fault injection to every external side-effect boundary: intent persisted, dispatch started, provider accepted, remote operation completed, receipt persisted, terminal state persisted.
- [x] Add a command-backed provider invoker that terminates a timed-out child process and has regression tests for timeout, environment isolation, output limits, and error redaction. This is a bounded process adapter, not a full sandbox.
- [x] Verify hard termination and process-tree termination: implementation is present for the tested platforms: POSIX uses process-group termination, and Windows uses a kill-on-close Job Object. The descendant-process regression passed on main SHA `2ece3959991ac41f9b104242e25a4e1f8153e85e`.
- [ ] Apply OS/container memory limits for untrusted provider execution. Byte-bounded output capture limits retained output, but does not enforce a process memory quota.
- [ ] Prove cancellation and absolute deadlines propagate end-to-end through supervisor, graph nodes, provider/model adapters, child processes, and validation commands.
- [ ] Prove unknown remote outcomes reconcile by idempotency key or authoritative operation lookup before retry. Never automatically replay an uncertain operation.

**Exit evidence:** bounded wall-clock tests, platform-specific process-tree termination, durable checkpoint snapshots, duplicate-side-effect counters, restart/reconciliation tests, and no false-success assertions.

### P0 — Canonical run, plan, progress, and evidence state

- [x] Durable execution controller, plan/run store, progress journal, and dashboard projections exist.
- [x] Supervisor task identity and allowlisted provider lifecycle events are propagated into progress records.
- [ ] Run a cross-store crash matrix over task state, plan/run revision, progress journal, executive receipt, and evidence index. Detect and reconcile split commits without replay.
- [ ] Prove dashboard and CLI use canonical durable status, not generated progress text; show blocked, cancelled, timed-out, unknown, stale, and unscored states distinctly.
- [ ] Verify shared memory search across agents and branches with project scope, access control, provenance, freshness, and retraction tests.

**Exit evidence:** crash/restart matrix, source-of-truth map, API/UI parity assertions, and searchable evidence references tied to exact revisions.

### P0 — Evaluator and isolation trust boundary

- [x] Repository contracts define separate isolation and evaluator attestations and fail-closed receipt checks.
- [ ] Bind production probe execution to a trusted launcher that enforces workspace/process/container isolation, egress restrictions, and no inherited secrets.
- [ ] Use an independently controlled evaluator, sealed corpus custody, external signer/key roots, expiry/revocation checks, and tamper-evident artifact lineage.
- [ ] Run adversarial tests for workspace escape, secret access, network access, evaluator manipulation, holdout exposure, and uncertain adoption/rollback.
- [ ] Keep evaluation `unscored` or `unknown` when evidence or trusted verification is missing. Never infer independence from a signature or repository CI.

**Exit evidence:** signed production attestations verified against out-of-band trust roots, negative isolation tests, and independent evaluator/corpus custody evidence.

### P1 — Standalone packaging and API

- [x] Portable bundle manifest, hash pinning, install/update/rollback mechanisms, CLI entry point, and data-only extension manifest exist.
- [x] Add a clean-install smoke test that installs the portable bundle and starts the installed CLI outside the source checkout without PYTHONPATH. This proves startup independence, not full offline operation or production isolation.
- [x] Publish the versioned Python SDK/API surface (SDK API v1). The stable local service contract and compatibility policy remain open. Preserve existing JSON contracts unless a versioned migration, tests, and rollback are provided.
- [ ] Verify package contents, dependency/SBOM inventory, offline behavior, platform matrix, upgrade compatibility, and rollback with user data preserved.
- [ ] Add install/upgrade/uninstall failure injection and tampered-bundle rejection tests.

**Exit evidence:** reproducible artifact, clean-environment install log, manifest/checksum, API compatibility tests, migration/rollback rehearsal, and release notes. Do not increment the product version for docs-only changes.

### P1 — Extensions and provider adapters

- [x] Data-only extension manifest validates names, semantic versions, capabilities, trust labels, permissions, output limits, and timeouts.
- [x] Command-backed provider invocation uses an absolute executable, explicit working directory, no shell, a minimal environment, a timeout, and a response byte check. This does not sandbox arbitrary extension code or strictly bound output memory before process exit.
- [ ] Add an adapter host that executes untrusted extensions in a bounded process with explicit IPC, no inherited secrets, restricted filesystem/network access, and forced termination.
- [ ] Add compatibility negotiation, permission grants, capability revocation, health/circuit-breaker behavior, bounded output, and pinned provider identity.
- [ ] Test malformed manifests, downgrade attempts, timeout, crash, oversized output, permission escalation, duplicate registration, and rollback.

### P1 — Learning and promotion

- [x] Evidence-aware learning, experiment control, and promotion/rollback contracts exist.
- [ ] Test duplicate and reordered receipts, stale/conflicting evidence, changed model/policy, interrupted stages, and idempotent ingestion.
- [ ] Require pinned baseline/candidate manifests, regression and transfer suites, uncertainty reporting, cost/latency limits, and verified rollback before promotion.
- [ ] Keep external validation and customer outcomes separate from repository test results.

### P1 — Documentation and release truth

- [x] README and Pages status now distinguish the verified implementation baseline from the later status-refresh commit.
- [ ] Refresh the roadmap issues against the current main SHA and attach evidence references for each claimed completed phase. This matrix is a dated snapshot; use live Actions and main history for current status.
- [ ] Keep `site/index.html` and `docs/index.html` byte-identical; run consistency tests and verify Pages deployment on the exact merged SHA.
- [ ] Publish a release-readiness matrix with limitations, skipped checks, open external gates, install/rollback instructions, and a source/artifact digest.

## Governance verification — 2026-10-23

- [x] The `mainBranchRule` ruleset is active and blocks branch deletion and non-fast-forward updates.
- [ ] Required status checks are **not configured** in that ruleset as inspected on 2026-10-23. The current ruleset contains only `deletion` and `non_fast_forward`; green CI is therefore not enforced by this ruleset as a merge prerequisite. A repository owner/admin must add the required check names (including the final status aggregator and the relevant runtime, architecture, hardening, harness, and portable-bundle checks) and verify the saved ruleset. This connector can inspect the ruleset but cannot modify repository administration settings.

## Merge and release rules

1. Use a feature branch and a reviewable PR for each bounded gate.
2. Run exact-head required checks. Fix failures before merge; skipped jobs are not passes.
3. Include regression tests and a rollback strategy for every runtime-affecting change.
4. After merge, verify the new main SHA, push-triggered workflows, and Pages deployment where relevant.
5. Close a gate only when its exit evidence exists. Do not mark external isolation, sealed-corpus custody, or independent evaluation complete based on local CI.


## Review refresh — 2026-10-25

**Main revision observed at review start:** `97099d84af3a799a99ac929e3920817a137639f8` (merge of PR #923). This is a review anchor, not a claim that all post-merge checks completed. Refresh the SHA again when running the final exit gate.

### Evidence-linked status

| Acceptance area | Current evidence | Status / remaining action |
|---|---|---|
| Adaptive workflow profile | `portable/workflow_profile.py`; `tests/test_workflow_profile.py`; `docs/ADAPTIVE_USER_WORKFLOW_POLICY.md` on the implementation branch | Profile schema, deterministic digest, validation, and preference precedence are implemented as a standalone contract. Runtime wiring to CLI/dashboard/execution controller, persisted profile on run receipt, and action-level tests remain open. |
| Arena #404 | [Issue #404](https://github.com/sourabhJainR/AUREN/issues/404) | Open. Independent operator/evaluator and sealed holdout custody, independently retained signed result, repeatability report, adversarial outcomes, uncertainty, and cost evidence are not established by repository CI. |
| Provider crash/reconciliation | [PR #925](https://github.com/sourabhJainR/AUREN/pull/925), [PR #924](https://github.com/sourabhJainR/AUREN/pull/924) | Open PRs observed during this refresh. Their exact-head checks and review must be checked live; neither proves complete cross-store or provider-native reconciliation. |
| Dashboard recovery and memory provenance | Existing durable progress/journal and memory modules; matrix gates above | Partial foundations. Still require a cross-store crash matrix, dashboard/CLI parity after restart and partial writes, cross-agent/branch access scope, freshness, conflict resolution and retraction tests. |
| Extension isolation | `portable/extension_contract.py` and its tests | Manifest validation and command adapter are not an OS sandbox. Restricted filesystem/network, no inherited secrets, resource limits, forced termination, malicious extension and revocation tests remain open. |
| Install/upgrade/rollback | `portable/auren_runtime.py` and portable bundle workflows | Mechanisms and clean-start coverage exist. Full clean-environment platform matrix, dependency/SBOM, tampered bundle, interrupted migration/upgrade/rollback, and user-data preservation rehearsal remain open. |
| Exact-head and post-merge checks | GitHub Actions runs attached to the exact commit/PR head; branch ruleset settings | Check current live results for the exact PR head and new main SHA. Queued/running/skipped/missing checks are not passes. Owner/admin must configure and verify required status checks if still absent. |
| Release artifact and rollback proof | Reproducible bundle manifest/hash mechanisms and release instructions | No release is authorized by this matrix update. Record source SHA, artifact SHA-256, manifest, platform smoke tests, signed provenance where available, and operator-run rollback evidence before release. |

### Workflow profile integration acceptance

- [x] Add a versioned immutable profile contract with strict allowed values and rejection of unknown fields.
- [x] Canonical digest and task > project > saved > safe-default resolution for complete profiles.
- [x] Unit tests for defaults, deterministic digest, invalid modes, unknown fields, unsupported version, precedence, and fail-closed invalid task profile.
- [ ] Wire profile resolution into the canonical supervisor/execution controller; do not create a second authority system.
- [ ] Persist profile version/digest with durable task intent and evidence receipt; reject incompatible resume.
- [ ] Implement commit-per-change, stage-only, working-tree and PR modes in repository adapters while preserving unrelated changes.
- [ ] Add interrupted action, dirty/untracked worktree, cancellation/restart, and rollback tests.
- [ ] Verify autonomous mode cannot bypass review, required checks, approval boundaries, or uncertain-side-effect reconciliation.
- [ ] Surface effective mode, approval boundary, durable state, and next action in CLI/dashboard.

### Final exit-gate procedure

1. Resolve the exact current `main` SHA and bind this matrix plus every evidence link to that revision.
2. For each criterion, attach a test/log/artifact or an independently controlled external attestation. Mark unavailable evidence `blocked`, not passed.
3. Verify review state and all required exact-head checks; explain each skipped job and rerun any required skipped/missing check.
4. Verify post-merge workflows on the resulting main SHA and Pages deployment where relevant.
5. Build from pinned source; retain bundle manifest and SHA-256 digest; install and smoke-test in a clean environment.
6. Execute staging migration, interruption, rollback, and user-data integrity rehearsal; record operator, environment, timestamps, logs, and before/after data checks.
7. Keep #404 and release blocked until external custody, signed results, extension isolation, and rollback evidence are independently verified.

### Workflow profile persistence follow-up — 2026-10-25

- [x] Add `portable/workflow_profile_store.py` for validated, canonical, atomic profile persistence.
- [x] Reject symlink targets, duplicate JSON keys, malformed or incompatible profiles, and oversized input; never silently replace invalid saved preferences with defaults.
- [x] Use restrictive file permissions on POSIX and fsync the file plus parent directory around atomic replacement.
- [x] Add tests for missing-vs-invalid state, canonical writes, private permissions, duplicate keys, size limits, symlinks, and refusing invalid writes.
- [ ] Run the exact-head test and required CI suites; results are not inferred from source inspection.
- [ ] Wire persisted profiles into supervisor task intent and durable receipts, then enforce effective modes in repository adapters. Storage alone does not change runtime behavior.
- [ ] Add crash/interruption tests for a profile write and task resume; reject incompatible profile versions on resume.


### Durable workflow-profile integration — 2026-10-25

- [x] Persist explicit saved/project profiles under the controller's `.auren` state directory using the validated atomic store.
- [x] Resolve task > project > saved preference precedence when a profile is configured.
- [x] Bind canonical profile data and SHA-256 digest into durable task intent; include profile version/digest in lifecycle evidence.
- [x] Validate profile version/digest before a claimed task can execute after restart; tampered or incompatible profile fails closed.
- [x] Preserve legacy task intent when no profile is configured to avoid changing existing stable work-key digests during upgrade.
- [ ] Run exact-head tests, including cross-platform durable runtime and full regression checks.
- [ ] Enforce delivery mode at repository action adapters; profile persistence is not proof that commit/stage/PR behavior was applied.
- [ ] Add crash injection between profile file write, task insertion, evidence projection and resume.
