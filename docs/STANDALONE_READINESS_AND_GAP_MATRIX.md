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
- [ ] Verify hard termination and process-tree termination on all supported platforms; POSIX kills the process group, while Windows currently terminates the direct child only. Apply OS/container memory limits for untrusted provider output.
- [ ] Prove cancellation and absolute deadlines propagate through supervisor, graph nodes, provider/model adapters, child processes, and validation commands.
- [ ] Prove unknown remote outcomes reconcile by idempotency key or operation lookup before retry. No automatic replay when completion is uncertain.

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

## Merge and release rules

1. Use a feature branch and a reviewable PR for each bounded gate.
2. Run exact-head required checks. Fix failures before merge; skipped jobs are not passes.
3. Include regression tests and a rollback strategy for every runtime-affecting change.
4. After merge, verify the new main SHA, push-triggered workflows, and Pages deployment where relevant.
5. Close a gate only when its exit evidence exists. Do not mark external isolation, sealed-corpus custody, or independent evaluation complete based on local CI.
