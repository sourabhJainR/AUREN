# Standalone Readiness and Important-Gap Matrix

**Reviewed:** 2026-10-28  
**Product version:** 1.0.6 (no release created by this review)  
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
- [x] Add repository regression coverage for selected local split-write boundaries across plan/run, recovery checkpoint, progress journal, executive receipt, evidence index, learning checkpoint, and dashboard state (PRs #1034, #1037, #1042).
- [ ] Extend fault injection to every external side-effect boundary: intent persisted, dispatch started, provider accepted, remote operation completed, receipt persisted, terminal state persisted. The selected tests do not prove every real provider's native idempotency or authoritative lookup.
- [x] Add a command-backed provider invoker that terminates a timed-out child process and has regression tests for timeout, environment isolation, output limits, and error redaction. This is a bounded process adapter, not a full sandbox.
- [x] Verify hard termination and process-tree termination: implementation is present for the tested platforms: POSIX uses process-group termination, and Windows uses a kill-on-close Job Object. The descendant-process regression is included in the terminal-successful Runtime Resilience Matrix on main SHA `5b3e3858d85d558945f2a4ccf65a18fe9b323ba7`.
- [ ] Apply OS/container memory limits for untrusted provider execution. Byte-bounded output capture limits retained output, but does not enforce a process memory quota.
- [ ] Prove cancellation and absolute deadlines propagate end-to-end through every supervisor, graph node, provider/model adapter, child process, and validation command. Selected live-handler and subprocess tests exist; universal adapter coverage and deployment evidence remain open.
- [ ] Prove unknown remote outcomes reconcile by idempotency key or authoritative operation lookup before retry. Never automatically replay an uncertain operation.

**Exit evidence:** bounded wall-clock tests, platform-specific process-tree termination, durable checkpoint snapshots, duplicate-side-effect counters, restart/reconciliation tests, and no false-success assertions.

### P0 — Canonical run, plan, progress, and evidence state

- [x] Durable execution controller, plan/run store, progress journal, and dashboard projections exist.
- [x] Supervisor task identity and allowlisted provider lifecycle events are propagated into progress records.
- [x] Add a combined repository regression slice for selected cross-store boundaries over task state, plan/run revision, recovery checkpoint, progress journal, executive receipt, evidence index, learning checkpoint, and dashboard projection. Tests require blocked/unknown state on disagreement and no handler replay (PRs #1034, #1037, #1042).
- [ ] Complete the remaining crash matrix across every provider-side-effect boundary and deployed store combination; repository fixtures do not prove real provider behavior or production recovery.
- [x] Verify selected dashboard projections use durable plan/run checkpoints, ignore stale progress events, and remain blocked on split-write disagreement; CLI worker-state tests preserve blocked state after reconciliation.
- [ ] Complete full dashboard/API/CLI parity for blocked, cancelled, timed-out, unknown, stale, and unscored states across deployed multi-session restart and partial-write scenarios.
- [ ] Verify shared memory search across agents and branches with project scope, access control, provenance, freshness, and retraction tests.

**Exit evidence:** crash/restart matrix, source-of-truth map, API/UI parity assertions, and searchable evidence references tied to exact revisions.

### P0 — Evaluator and isolation trust boundary

- [x] Repository contracts define separate isolation and evaluator attestations and fail-closed receipt checks. The production `ExternalEvaluatorGateway` rejects test-only verifier mode unless explicitly opted in and requires both a trusted custody-verification boundary and a `production_ready` OS isolation adapter.
- [ ] Supply and independently validate a production OS isolation adapter for the evaluator gateway. The existing Docker Arena runner has network disabled, no host mounts, read-only root, non-root UID/GID, dropped capabilities, no-new-privileges, and CPU/memory/PID limits, but it is not yet wired as a production evaluator gateway adapter.
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

## Governance verification — 2026-10-28

- [x] The `mainBranchRule` ruleset is active and blocks branch deletion and non-fast-forward updates.
- [ ] The active `mainBranchRule` ruleset currently contains only `deletion` and `non_fast_forward`; it has no required-status-check rule. The branch-protection endpoint was not readable through the connected GitHub integration, so its separate settings could not be verified. A repository owner/admin must configure and verify effective required checks, including the final status aggregator and relevant runtime, architecture, hardening, harness, and portable-bundle checks. This integration cannot change repository administration settings.

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


### Arena uncertainty reporting follow-up — 2026-10-25

- [x] Add packet schema v2 with an uncertainty summary bound by the campaign content digest and external attestation.
- [x] Validate method, sample size, confidence level, interval bounds, and unknown-result count; reject malformed or contradictory reports.
- [x] Keep schema v1 readable for historical compatibility but explicitly report that it lacks the new uncertainty acceptance evidence.
- [ ] Run exact-head CI and adversarial regression checks.
- [ ] Obtain externally controlled signed v2 campaign evidence. Local schema tests do not close issue #404 or prove independent custody, signer identity, or an actual campaign run.


## Verified engineering snapshot — 2026-10-28

**Review baseline main SHA:** `5b3e3858d85d558945f2a4ccf65a18fe9b323ba7`.

- Current-main check-runs are terminal: 28 successful, zero skipped, zero failed, zero pending.
- All six post-merge push workflows completed successfully: [AI Harness Check](https://github.com/sourabhJainR/AUREN/actions/runs/40627642942), [AUREN Portable Bundle](https://github.com/sourabhJainR/AUREN/actions/runs/40627642859), [Runtime Resilience Matrix](https://github.com/sourabhJainR/AUREN/actions/runs/40627642902), [Architecture Integrity](https://github.com/sourabhJainR/AUREN/actions/runs/40627642850), [Production AI Harness Hardening](https://github.com/sourabhJainR/AUREN/actions/runs/40627642868), and [Agent Capability Runtime](https://github.com/sourabhJainR/AUREN/actions/runs/40627642995).
- Portable artifacts from [run 40627642859](https://github.com/sourabhJainR/AUREN/actions/runs/40627642859) were downloaded and verified. Outer bundle artifact SHA-256: `f87dbad0bc9e6de7da9ea7c21226d4710e5281d797a8e3f0c289eb4d42ef56d8`. Outer checksum artifact SHA-256: `9c3d195fed261c858d1475b7f4ba12977e32da9a24107da535c7ec6a7be989e0`. Embedded ZIP SHA-256: `7772704781caf23c3b329d7fc5e76ee4492a9f8fd4a88b49140bbe61c1972be9`. All 508 manifest entries match their recorded sizes and hashes; the manifest pins this SHA, version `1.0.6`, and `target_repository_mutation=false`.
- Repository tests now cover selected #692/#694 shared identity, evidence-gated learning, cross-store split writes, fresh-process reconciliation, no replay, cancellation/deadline blocking, and dashboard status derived from durable state. No local test run is claimed for this review because no AUREN checkout/runtime was available; the evidence is from the recorded GitHub CI runs.
- #998 repository-level graph recovery and independent-verifier code gates are recorded as complete; external production isolation and independent evaluator/holdout custody remain blocked. #534 remains open until operator-controlled staging migration, fresh-process resume, rollback, and post-cutover reconciliation receipts are attached and independently checked.

This snapshot does not prove deployment readiness, independent evaluation, sealed-holdout custody, production isolation, or AGI/SI. A documentation rollback is a revert of this change; no runtime behavior or product version is changed.


## Verified engineering snapshot — 2026-10-30

**Review baseline main SHA:** `3094fb0bdbebfb19702a000cc6e3577e9eb4f39d`.

- Current-main check-runs are terminal: 23 succeeded, 2 were skipped, zero failed, zero pending. The exact skipped checks are `hardening` and `Durable runtime (${{ matrix.os }}, Python ${{ matrix.python-version }})`; neither is counted as a pass.
- All six inspected post-merge push workflows completed successfully: [Agent Capability Runtime](https://github.com/sourabhJainR/AUREN/actions/runs/41059760789), [Architecture Integrity](https://github.com/sourabhJainR/AUREN/actions/runs/41059760966), [AI Harness Check](https://github.com/sourabhJainR/AUREN/actions/runs/41059760907), [Production AI Harness Hardening](https://github.com/sourabhJainR/AUREN/actions/runs/41059760852), [Runtime Resilience Matrix](https://github.com/sourabhJainR/AUREN/actions/runs/41059760752), and [AUREN Portable Bundle](https://github.com/sourabhJainR/AUREN/actions/runs/41059760737).
- Portable artifacts from [run 41059760737](https://github.com/sourabhJainR/AUREN/actions/runs/41059760737) were downloaded and verified. Outer bundle artifact SHA-256: `35862ac41f061b05f08d3ae9cc208844a6dfea44a25a2d2c570142c59eeffa67`. Outer checksum artifact SHA-256: `aead9ee0552530467c79dd2b9416cdc020770eb81d1da1bd79499773702f7a0b`. Embedded ZIP SHA-256: `2ad246c83878e772d5c3bbd6221d9a49a7e961ca0f5a9f3bbbac207a7b65e104`. All 517 manifest entries match their recorded sizes and hashes; the only unmanifested generated entry is `payload/PORTABLE_BUNDLE.txt`. The manifest pins this SHA, version `1.0.6`, and `target_repository_mutation=false`.
- **#998 graph recovery:** PRs #1009, #1010 and #1011 are merged. Current main includes checkpoint/journal and apply/finalize crash recovery, unknown cancellation/termination handling, stale plan/intent/target-head rejection, overlapping/duplicate adoption rejection, verifier self-approval rejection, and missing/stale/contradictory evidence tests. This is repository-level implementation and CI evidence only.
- **#692/#694:** current main contains shared durable identity, cross-store split-write reconciliation, fresh-process resume, no-replay delivery, cancellation/deadline propagation, evidence-gated learning and dashboard projection from durable state. These remain open for independent evaluator/holdout custody, deployment parity and operator evidence.
- **#534 remains blocked** until an operator-controlled staging migration, fresh-process resume, rollback and post-cutover write-reconciliation receipts are attached and independently checked. Preserve both state roots.
- **#1055 and #1140 remain open:** role-aware dispatch still needs host-owned identity verification and independent production isolation evidence; the response chunk/ack adapter still needs a real chunk-capable host boundary with receiver-confirmed acknowledgements. Atomic MCP frames remain fail-closed.
- The living radar refresh is tracked in [PR #1143](https://github.com/sourabhJainR/AUREN/pull/1143); exact-head checks must pass before merge.

No local test run is claimed for this review because no AUREN checkout/runtime is available. The evidence above comes from repository source inspection, GitHub CI and downloaded artifact verification. This snapshot does not prove deployment readiness, independent evaluation, sealed-holdout custody, production isolation or AGI/SI. A documentation rollback is a revert of this change; no runtime behavior or product version is changed.

## Response-delivery completion update — 2026-11-01

**Merged implementation:** PR #1165 at 357d60af09f1be486e0950de727077454efceac1.
**Baseline main before this change:** 40bd0bd98a68ddabfad74aeca26b2a5a84ef2b1b.

- [x] Extend the shared RuntimeExecutionGuard with a checkpointable UTF-8 delivery-byte ledger and idempotent operation keys; wire the acknowledged response transport to the same guard instance.
- [x] Add slow-receiver backpressure, disconnect/unknown-outcome, acknowledged restart/resume, and bounded-worker queue-saturation regressions. Existing HTTP integration tests also exercise stale lease recovery, cancellation, deadline, and duplicate acknowledgement.
- [x] Add CLI stdin/stdout and file output contracts. Oversized input is rejected before output; file output is atomically committed; partial stdout writes are explicitly unknown, not reported complete.
- [x] Add an MCP regression that sends an oversized atomic JSON-RPC frame while a descendant is running and verifies the process tree is terminated. MCP framing remains atomic; no frame truncation or response chunking is introduced.
- [x] Expose durable delivery metrics through Session Control Center session and control-center APIs.
- [x] Add a paired baseline/candidate benchmark and CI artifact workflow for integrity, recovery, latency, peak traced memory and transported-byte cost proxy.
- [x] PR #1165 exact-head CI passed all nine workflows; all eight inspected post-merge push workflows passed on the merge SHA.
- [x] Paired benchmark artifact [run 41266288196](https://github.com/sourabhJainR/AUREN/actions/runs/41266288196) inspected: five iterations, 299,008-byte payload; baseline/candidate integrity and completion passed in every iteration; recovery resumed from chunk 2 with no acknowledged-chunk replay.

| Measure | Baseline | Candidate | Difference |
|---|---:|---:|---:|
| Median latency | 279.032 ms | 277.653 ms | -1.379 ms |
| Peak traced memory | 3,262,528 B | 2,181,992 B | -1,080,536 B |
| Transported bytes | 299,008 B | 299,008 B | 0 B |
| Chunks | 74 | 74 | 0 |

Provider monetary cost is unmeasured; transported bytes are a proxy only. This local benchmark does not establish production latency or cost improvement.
- [ ] Provider monetary cost remains unmeasured without a metered provider; production receiver latency/memory and deployment-wide queue saturation require a staged workload.
- [x] Add cross-process worker-slot leases with heartbeat renewal, owner-fenced release, and crash expiry in PR #1166; all six post-merge workflows passed on main SHA `81906e3eca59e9903b6056a5493abd736cc62309`.
- [ ] Complete persistent pending-queue admission limits and deployment-wide multi-host contention validation.
- [ ] Complete independent production isolation, external evaluator/holdout custody, owner/admin required-check enforcement, and operator staging rollback rehearsal.

The implementation status above is source/test intent, not a passing-CI claim. Keep the product version unchanged. Revert the PR as a unit if required; do not remove the existing byte caps, digest-bound acknowledgement, unknown-outcome block, or atomic MCP frame rejection.


### Response-delivery benchmark evidence — 2026-11-01

**Baseline:** `40bd0bd98a68ddabfad74aeca26b2a5a84ef2b1b`.
**Candidate PR head:** `3d0cfa9ff6ecfbdfb3f163a26855c58af80356f1`.
**Benchmark merge revision:** `8b6cf7a0e4fc13821b129adaf9415bb2bfb8323c`.
**Artifact:** [Response delivery benchmark run 41263044268](https://github.com/sourabhJainR/AUREN/actions/runs/41263044268), artifact `response-delivery-benchmark-8b6cf7a0e4fc13821b129adaf9415bb2bfb8323c`.

Five paired iterations on Python 3.12.15 / Ubuntu runner, 299,008 UTF-8 payload bytes:

| Measure | Baseline | Candidate | Difference |
|---|---:|---:|---:|
| Median latency | 159.463 ms | 156.201 ms | -3.262 ms |
| Peak traced memory | 3,262,528 B | 2,181,992 B | -1,080,536 B |
| Transported bytes | 299,008 B | 299,008 B | 0 B |
| Chunks | 74 | 74 | 0 |

- [x] All baseline and candidate iterations preserved integrity and completed delivery.
- [x] Interrupted recovery resumed after two acknowledged chunks, completed the response, and did not replay acknowledged chunks.
- [x] The benchmark artifact is published and retained by Actions.
- [ ] Monetary provider cost remains unmeasured because this local benchmark has no metered provider. Transported bytes are only a cost proxy.
- [ ] Production receiver latency/memory, concurrent multi-host queue behavior, and operator-controlled staging rollback remain external deployment evidence.

The result is a controlled local comparison, not a production performance guarantee or a promotion decision. Exact-head PR CI remains the merge gate.



## Software-team and capability-lab update — 2026-11-01

**AUREN branch:** `feat/issue-696-delivery-completion`; PR [#1165](https://github.com/sourabhJainR/AUREN/pull/1165). Latest exact-head CI must pass before merge. The delivery integration test suite exposed lifecycle/recovery regressions; these are being corrected on the branch and are not waived.

- [x] Product positioning and About/README now describe AUREN as a supervised operating system for an AI engineering company.
- [x] Canonical graph crew definitions include solution architect, engineering manager, research/evaluation, release/operations, CTO sponsor, and CEO sponsor personas.
- [x] `build_software_team_persona_adapters` creates proposal-only adapters for these six roles with explicit per-persona read-only tool scopes. This is additive to the existing eight core specialist adapters.
- [x] Persona contract and adapter tests are included in the portable-bundle CI test list.
- [x] The role model documents reporting flow, independent QA/security, manager acceptance, stakeholder evidence, and explicit human approval boundaries.
- [x] Independent private research repository [firstTestproj](https://github.com/sourabhJainR/firstTestproj) is initialized as the AUREN Capability Lab. Its latest CI run [41264707461](https://github.com/sourabhJainR/firstTestproj/actions/runs/41264707461) passed.
- [x] First lab experiment compared shared context with isolated persona context using a deterministic fixture: 27 vs 19 context fields (29.6% fewer), unchanged 100% acceptance/evidence coverage, and no QA exposure to implementer self-assessment. See [EXPERIMENT_RESULTS.md](https://github.com/sourabhJainR/firstTestproj/blob/main/EXPERIMENT_RESULTS.md).
- [x] Lab evaluator requires independently reviewed holdouts, cross-domain coverage, and bounded confidence calibration before research promotion eligibility. Development-only success cannot pass the promotion gate.
- [ ] Real provider/model adapters, metered provider cost, token/memory measurements, sealed holdouts, broad transfer, and external independent evaluation remain outstanding. The deterministic experiment does not demonstrate model-level SI gains or AGI.
- [x] GraphAgentTeam exposes the six new personas and a complete, fail-closed `register_software_team_personas()` path; tests cover successful registration and partial-registration rejection.
- [ ] Production host bootstrap must supply approved planner/model and per-persona tool-scope mappings. Production credentials and authority are not fabricated or enabled by default.

### Roadmap reconciliation

| Gate | Current status | Remaining proof |
|---|---|---|
| #696 unified runtime, learning, delivery, dashboard | In progress; PR #1165 | Exact-head CI, merge/main verification, host persona registration, complete delivery lifecycle evidence |
| #692 recursive harness learning | Research/evaluation foundation added | Causal attribution, pinned baselines, sealed holdouts, transfer, independent Arena custody |
| #694 spec-driven persistent planning | Engineering-manager and architect contracts added | Durable plan evolution, dependency updates, fresh-context resume, contradiction and staleness tests |
| #809 durable human approval and resumable side effects | Not closed by this change | Real host approval boundary, restart, cancellation, timeout, idempotency and final-ack tests |
| #534 migration and rollback | Blocked | Operator-controlled staging migration, fresh-process resume, rollback and post-cutover reconciliation receipts |
| Independent Arena / isolation | Not production-ready | External custody, repeatability, adversarial holdouts, OS-level isolation audit and signed attestation |

**Readiness decision:** the repository now has a more explicit software-company team model and an independent capability experiment harness. It is not a production-ready autonomous engineering company, and it has not demonstrated AGI. Do not promote the new persona adapters to production or close the remaining roadmap gates until the evidence above is attached and independently reviewed. Product version remains unchanged. Rollback is a revert of the relevant PR; the separate lab has no runtime dependency on AUREN.


### Refreshed delivery benchmark — exact candidate revision `81bc9d74ba64a5531d90690db3c8139842011c7f`

Artifact: [Response delivery benchmark run 41264913663](https://github.com/sourabhJainR/AUREN/actions/runs/41264913663), artifact `response-delivery-benchmark-81bc9d74ba64a5531d90690db3c8139842011c7f`.

Five paired iterations on Python 3.12.15 / Linux runner, 299,008 UTF-8 payload bytes:

| Measure | Baseline | Candidate | Difference |
|---|---:|---:|---:|
| Latency | 173.865 ms | 168.394 ms | -5.471 ms |
| Peak traced memory | 3,262,528 B | 2,181,992 B | -1,080,536 B |
| Transported bytes | 299,008 B | 299,008 B | 0 B |
| Chunks | 74 | 74 | 0 |

Both baseline and candidate passed integrity and completion in all iterations. Recovery resumed after two acknowledged chunks, completed the response, and did not replay acknowledged chunks. Provider monetary cost remains unknown; transported bytes are only a proxy. These local measurements do not establish production latency, multi-host contention, or deployment readiness.


### Additional benchmark repeat — 2026-11-01

Artifact: [Response delivery benchmark run 41265374509](https://github.com/sourabhJainR/AUREN/actions/runs/41265374509), candidate revision `0e83780125ed9059ea3470b1c85d861ac0be9256`.

Five paired iterations, same 299,008-byte payload and Python 3.12.15 / Linux runner:

| Measure | Baseline | Candidate | Difference |
|---|---:|---:|---:|
| Latency | 282.188 ms | 283.269 ms | +1.081 ms |
| Peak traced memory | 3,262,528 B | 2,181,992 B | -1,080,536 B |
| Transported bytes | 299,008 B | 299,008 B | 0 B |
| Chunks | 74 | 74 | 0 |

Integrity and completion passed all five iterations; recovery resumed from chunk 2 with no replay of acknowledged chunks. This repeat does **not** show a latency improvement; the candidate was 1.081 ms slower in this run. The stable observed benefit is lower peak traced memory, with identical transported bytes. Provider monetary cost remains unknown. Treat latency as environment-sensitive and do not claim a general speedup from these small local runs.


### Latest delivery evidence and CI — 2026-11-01

**Exact branch head:** `95156a49cd5e182e017d2d44ab9a313f9430b20c`.  
**Portable bundle and live delivery integration:** [run 41265759848](https://github.com/sourabhJainR/AUREN/actions/runs/41265759848) — passed deterministic contracts, live dashboard delivery integration tests, host entrypoint compilation, canonical bundle verification, checksum, and exact source pin.

**Latest paired benchmark:** [run 41265759849](https://github.com/sourabhJainR/AUREN/actions/runs/41265759849), artifact candidate revision `ce754e39064450e9d6092ec710d5145c47160a78`.

Five paired iterations on Python 3.12.15 / Linux, 299,008-byte payload:

| Measure | Baseline | Candidate | Difference |
|---|---:|---:|---:|
| Latency | 285.335 ms | 282.141 ms | -3.194 ms |
| Peak traced memory | 3,262,528 B | 2,181,992 B | -1,080,536 B |
| Transported bytes | 299,008 B | 299,008 B | 0 B |
| Chunks | 74 | 74 | 0 |

Both variants preserved integrity and completed all iterations. Recovery resumed from chunk 2 without replaying acknowledged chunks. Provider USD cost remains unmeasured; transported bytes are a proxy only. The other PR-level required workflows and branch policy must still pass before merge. The latest bundle success is not a substitute for the complete exact-head check set.


### Final paired benchmark for current software-team integration head — 2026-11-01

Artifact: [run 41266093867](https://github.com/sourabhJainR/AUREN/actions/runs/41266093867), candidate revision `cf18e34c47e2e6fa3c8c3ec1acdbd61b37b6bc54`.

Five paired iterations, 299,008-byte payload, Python 3.12.15 / Linux:

| Measure | Baseline | Candidate | Difference |
|---|---:|---:|---:|
| Latency | 169.443 ms | 168.294 ms | -1.149 ms |
| Peak traced memory | 3,262,528 B | 2,181,992 B | -1,080,536 B |
| Transported bytes | 299,008 B | 299,008 B | 0 B |
| Chunks | 74 | 74 | 0 |

All baseline and candidate iterations preserved integrity and completed. Recovery resumed after two acknowledged chunks and did not replay them. Monetary provider cost is not measured. The repeated runs show a consistent reduction in peak traced memory, while latency differences remain small and environment-sensitive. No production performance claim is made.


### Benchmark variability check on software-team integration head — 2026-11-01

Artifact: [run 41266198581](https://github.com/sourabhJainR/AUREN/actions/runs/41266198581), candidate revision `619588d13967c205ff5c77aadbd6c48e704cc674`.

Five paired iterations, 299,008-byte payload, Python 3.12.15 / Linux:

| Measure | Baseline | Candidate | Difference |
|---|---:|---:|---:|
| Latency | 183.868 ms | 190.066 ms | +6.198 ms |
| Peak traced memory | 3,262,528 B | 2,181,992 B | -1,080,536 B |
| Transported bytes | 299,008 B | 299,008 B | 0 B |
| Chunks | 74 | 74 | 0 |

Integrity and completion passed all iterations; recovery resumed from chunk 2 without replay. This run is slower on latency while retaining the same peak-memory reduction and byte count. Across repeats, latency moves in both directions; no reliable latency improvement is established. The memory result is stable in this harness, but still requires production receiver validation. Provider monetary cost remains unmeasured.


## Delivery completion and admission follow-up — 2026-11-01

- PR [#1165](https://github.com/sourabhJainR/AUREN/pull/1165) is merged at `357d60af09f1be486e0950de727077454efceac1`. All nine exact-head workflows passed. On the merge SHA, seven inspected push workflows passed; the Runtime Resilience Matrix is still running. The post-merge check set is not fully terminal until that run finishes.
- The merged delivery work covers shared crew-budget charging before HTTP chunk offers, idempotent acknowledged-chunk charging, slow-receiver backpressure, disconnect/unknown-outcome blocking, restart/resume, bounded worker saturation, CLI/stdout and atomic file delivery, MCP oversized atomic-frame rejection/cleanup, and durable Session Control Center metrics.
- Paired local benchmark results preserve integrity and recovery and show a repeatable reduction in traced peak memory in this harness. Latency varies in both directions. Monetary provider cost is not measured; transported bytes are a proxy only. Do not claim a production latency or cost improvement.
- PR [#1166](https://github.com/sourabhJainR/AUREN/pull/1166) merged at `81906e3eca59e9903b6056a5493abd736cc62309`; all six post-merge workflows passed. It adds SQLite-backed renewable worker-slot leases, crash expiry, and owner-fenced release across processes sharing one registry database.
- The eight-worker quota is scoped to processes sharing one delivery registry. Durable pending tasks are separately capped per shared ExecutionController database by a persisted admission policy (default 256); neither SQLite policy coordinates distinct roots/databases. Deployment-wide multi-host admission and operator testing remain open.
- Remaining #696 gates include real host bootstrap configuration for software-team persona/model/tool scopes; externally controlled evaluator and sealed holdout custody; independent OS-level isolation audit; operator-controlled #534 migration/rollback rehearsal; real provider idempotency and ambiguous-outcome reconciliation; and verified required-check enforcement by a repository owner/admin.

No local test run is claimed because this session has GitHub source/CI access but no AUREN checkout. Close the remaining gates only with exact-revision CI, operator receipts, or independently verifiable external evidence. Repository CI is not evidence of AGI or production autonomy.


## Follow-up verification — 2026-11-01

- PR [#1165](https://github.com/sourabhJainR/AUREN/pull/1165) is merged at `357d60af09f1be486e0950de727077454efceac1`; all nine exact-head workflows and all eight post-merge push workflows passed.
- PR [#1166](https://github.com/sourabhJainR/AUREN/pull/1166) is merged at `81906e3eca59e9903b6056a5493abd736cc62309`; all six post-merge workflows passed. It enforces the eight-worker quota across processes sharing the same registry database with renewable leases and crash expiry.
- PR [#1169](https://github.com/sourabhJainR/AUREN/pull/1169) adds a real nine-process contention regression and makes pytest execute the capacity suite. An initial explicit pytest run found two fake-thread tests that no longer matched the durable lease contract; those tests were rewritten to exercise slot admission directly. The latest exact-head CI is pending. Do not merge until the new pytest step and all required workflows pass.
- The local paired benchmark shows unchanged bytes/chunk count and complete integrity/recovery. Latency is small and variable; provider monetary cost remains unknown. The consistent traced-memory reduction is a local-harness observation, not a production guarantee.
- The separate [AUREN Capability Lab](https://github.com/sourabhJainR/firstTestproj) main SHA `5c7d9402dcf8eacee5fcc22f1f6c7a31271f9f5f` passed CI. Its deterministic persona-context experiment is a workflow-contract result, not model-level SI or AGI evidence.



## Current delivery, software-team and #696 reconciliation — 2026-11-01

### Merged implementation

| PR | Verified result |
|---|---|
| #1165 | Supervisor-owned response delivery, shared-budget charging, CLI/stdout, atomic file output, MCP atomic-frame rejection and cleanup |
| #1166 | Renewable SQLite worker-slot leases; eight concurrent workers across processes sharing one registry; crash expiry and owner-fenced release |
| #1167 | Real host registration for CEO, CTO, engineering manager, architect, research/evaluation and release/operations personas with explicit provider and tool-scope contracts |
| #1169 | Discoverable regression tests with nine competing OS processes against the eight-slot quota |
| #1170 | Live Session Control Center delivery-health panel |
| #1172 | Dashboard host accepts the exact caller-owned `RuntimeExecutionGuard` plus stable budget ID; host can resume a previously persisted budget |

### In progress

- **#1175 — durable delivery telemetry:** records total bytes/chunks, peak in-flight chunk bytes, receiver/backpressure wait, worker resumes, safe failure categories and completion digests. The Session Control Center renders these measurements. Exact-head CI is running; do not treat the PR as merged.
- **#1176 — fail-closed budget resume:** rejects a supplied budget ID when its durable checkpoint is missing or unreadable, before task dispatch. This closes the risk of silently creating a new budget under an existing external identity. Exact-head CI and review remain required.
- **#1174 — readiness documentation:** this PR refreshes the project description and this matrix against the latest merged baseline.

### Latest paired baseline-versus-candidate evidence

Artifact: [benchmark run 41275927868](https://github.com/sourabhJainR/AUREN/actions/runs/41275927868), five paired iterations, baseline `98730d8c9323bc1090c83fd23c1470586d882ba3`, candidate `2d3f8e6a886562a22ce84c7d93cbf55f24142c6f`, Python 3.12.15 / Linux, 299,008-byte payload.

| Measure | Baseline | Candidate | Difference |
|---|---:|---:|---:|
| Median latency | 194.365 ms | 192.191 ms | -2.174 ms (-1.12%) |
| Median peak traced memory | 3,262,528 B | 2,182,192 B | -1,080,336 B (-33.1%) |
| Transported bytes | 299,008 B | 299,008 B | No change |
| Chunks | 74 | 74 | No change |
| Integrity / completion | All pass | All pass | Preserved |

Recovery interrupted after two acknowledged chunks, resumed from chunk 2, delivered the remaining 72 chunks, and did not replay acknowledged chunks. Provider monetary cost is not measured; transported bytes are a proxy only. These are local harness results, not production performance guarantees.

### Independent SI capability lab

The separate [firstTestproj Capability Lab](https://github.com/sourabhJainR/firstTestproj) is at main SHA `5c7d9402dcf8eacee5fcc22f1f6c7a31271f9f5f`; its offline CI passed. It now tests:
- shared-context baseline versus isolated persona context;
- independent QA without implementer self-assessment exposure;
- task-scoped memory that admits only reviewed evidence to architect, research, implementation and manager roles;
- fresh-context QA and security review, excluding unreviewed and cross-task memories.

These tests establish workflow contracts with deterministic fixtures. They do not establish a model-level SI gain or AGI. Real model/provider adapters, metered provider cost, sealed external holdouts and broad cross-domain transfer remain outstanding.

### Remaining #696 roadmap gates

| Gate | Current status | Evidence still required |
|---|---|---|
| #696 unified runtime, learning, delivery and dashboard | In progress; core delivery, host personas and dashboard UI merged; telemetry and fail-closed budget resume under review | Final exact-head CI, production host configuration, full end-to-end lifecycle and external readiness evidence |
| #692 recursive harness learning | Independent lab and reviewed-memory routing implemented | Causal attribution, real model adapters, pinned baselines, sealed holdouts, transfer and independent Arena custody |
| #694 spec-driven persistent planning | Architect/manager personas and task-scoped evidence context implemented | Durable plan evolution, dependency changes, fresh-context resume, contradiction and staleness tests |
| #809 durable human approval and resumable side effects | Not closed | Real host approval boundary; provider idempotency; restart, cancellation, deadline, timeout and final-ack proof |
| #534 migration and rollback | Blocked | Operator-controlled staging migration, fresh-process recovery, rollback and post-cutover reconciliation receipts |
| Independent Arena / OS isolation | Not production-ready | External custody, repeatable adversarial holdouts, OS-level isolation audit and signed attestation |

Additional operational gaps:
- The eight-worker quota applies only to processes sharing the same registry database. Distinct registries and deployment-wide multi-host coordination remain unproven.
- The durable pending queue has a persisted per-ExecutionController-database cap (default 256), enforced transactionally across processes sharing that database. A deployment-wide quota across distinct roots/databases is not yet enforced.
- Real provider-side-effect ambiguity and cross-adapter idempotency still need production receipts.
- Required-check enforcement must be verified by a repository owner/admin; CI results alone do not prove the branch rules are configured correctly.
- No staging migration or rollback rehearsal has been claimed.

**Readiness decision:** AUREN now has the core response-delivery paths, cross-process worker admission, explicit shared-budget host injection, software-company personas, and live delivery observability. It is not yet production-ready as an autonomous engineering company and has not demonstrated AGI. Keep #696, #692, #694, #809, #534 and the independent Arena/isolation gates open until their specific evidence is independently reviewed. Revert each implementation PR as a unit if regression evidence requires rollback.


## Current-main evidence and capability radar — 2026-11-01

### Exact repository snapshot

- Main SHA: `1d6534d4ea533fe034a4258a131e8a03b9bc4b98`.
- Current-main check-runs: 25 terminal; 23 succeeded, two skipped, zero failed, zero pending. The skipped hardening and durable-runtime checks remain skipped, not passed.
- All six post-merge push workflows succeeded: Agent Capability Runtime, Architecture Integrity, AI Harness Check, Production AI Harness Hardening, Runtime Resilience Matrix and AUREN Portable Bundle.
- Portable bundle source commit is the exact main SHA and version is 1.0.6. Bundle artifact SHA-256: `bbec2aae1993fddfa0e6c9c36537398ff59048c55e5c3e87f10cc78ccc7460ff`; checksum artifact SHA-256: `20a27e5dcc6adc0fb953356a9b90a0e1d4f39b3881123ca34d9ddf3fc3dede3b`; embedded ZIP SHA-256: `7ba4719e83ffb30fcd5daed75e072d4bc64d5a041e89e78c6d96d38f405734fa`. All 524 manifest entries match their sizes and digests. The only expected generated entry outside the manifest is `payload/PORTABLE_BUNDLE.txt`. The manifest declares `target_repository_mutation=false`.
- PR #1183 remains open for trusted budget/approval resolution at the composition root; its observed head was `e0db3a6699d44c912f290f143874fd3ec6570d7c`. PR #1184 contains the radar refresh; its observed head was `992be8fbd6d8b90e848a97f5526ecb62708aeced`. Both exact-head workflow sets were queued/running at this snapshot. Do not merge either PR until every applicable check is terminal and successful.
- The active main ruleset exposes deletion and non-fast-forward protections only. No required-status-check rule was visible; the branch-protection endpoint returned 403 to the integration. Repository-owner confirmation of required checks is still needed.
- No local checkout/runtime was available, so no local tests or process-level fault-injection results are claimed.

### #692 and #694 integration evidence inspected

Current main already contains the canonical integration coverage requested for these lanes:

| Requirement | Existing regression coverage |
|---|---|
| Stable durable identity and duplicate submit | `tests/test_engineering_plan_recovery.py::test_learning_delivery_share_identity_and_reconcile_split_write_after_restart`; `tests/test_auren_runtime_worker.py::test_worker_cli_binds_stable_work_key_to_durable_controller_lifecycle` |
| Evidence-gated learning | Verified learning checkpoint, manifest/plan digest binding, verification evidence, and no adoption when cancelled, expired or evaluator is missing in `tests/test_engineering_plan_recovery.py` |
| Cross-store split-write reconciliation | Injected plan-store/recovery-ledger failure, explicit reconciliation, process-death and terminal-receipt crash tests in `tests/test_engineering_plan_recovery.py` |
| Fresh-process restart/resume and no replay | Restarted plan bridge and ExecutionController reusing the same work key and task ID; interrupted provider-charge and recovery tests in `tests/test_adaptive_runtime_recovery_integration.py` |
| Cancellation and absolute deadline | GraphAgentTeam and model-router propagation tests in `tests/test_execution_controller_cancellation.py`; delivery cannot complete on cancel/deadline/missing evaluator in `tests/test_engineering_plan_recovery.py` |
| Dashboard derived from durable state | `tests/test_engineering_dashboard.py` checks canonical plan status, stale journal suppression, corrupt or conflicting stores as unknown, and missing terminal journal as unknown rather than success |

These tests establish repository contracts. They do not prove provider-side idempotency, production parity, external evaluator independence, sealed-holdout custody or long-duration deployment behavior. Keep #692 and #694 open for those external gates.

### External capability radar decision

The 2026-11-01 refresh adds WeaveBench and AgentHorizon for long-horizon GUI/CLI/code evaluation; Agent Workflow Optimization and WorkflowGen for dynamic workflow experiments; NVIDIA OpenShell and OpenAI sandbox guidance for host-enforced boundaries; ORACLE, ParetoBandit and EvoRoute for model routing/lifecycle; ServiceNow AI Passport for delegated agent identity and revocation; and Co-Scientist/MechHypoBench for scientific hypothesis validation.

These are comparison inputs only. Reuse TaskPlan, StateGraph, ExecutionController, Guard, RuntimeExecutionGuard, LearningSteward, evidence graph and Arena. No duplicate orchestrator or external runtime dependency. Do not enable candidate routing or extensions by default without paired measured value, independent evaluation and rollback.

### Still blocked

- **#534:** no operator-controlled staging migration, fresh-process resume, rollback or post-cutover reconciliation receipts are attached and independently checked. Preserve both state roots.
- **Production isolation:** no independent host/kernel/network/secret isolation audit.
- **Evaluator independence:** no separately controlled evaluator identity, sealed holdout custody or signed external campaign receipt.
- **Scientific discovery:** no independent domain replication or expert-scored AUREN campaign.
- **AGI/SI:** no claim is supported by internal CI, product announcements or these research benchmarks.
