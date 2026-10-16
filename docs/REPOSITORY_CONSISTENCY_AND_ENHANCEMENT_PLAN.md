# AUREN Repository Consistency Review and Enhancement Completion Plan

**Review baseline:** `401902f24fa87a968300d96be095bdc96e78fad8`  
**Review date:** 2026-10-14

This is a refreshed status pass against the current mainline after state-migration recovery coverage was merged. Historical findings below are retained for traceability; current statuses are stated explicitly.  
**Purpose:** record evidence-backed consistency findings, distinguish verified implementation from intended architecture, and order the remaining work without duplicating existing control planes.

## Scope and confidence

The review inventoried the complete GitHub tree (1,045 entries), default branch, release metadata, workflow configuration and recent Actions runs. It then inspected the canonical agent instructions and skill mirrors, graph APIs, adaptive runtime, durable execution controller, autonomous supervisor, recovery ledger, architecture/arena documentation, and the related tests/issues.

This is a deep review of the main architectural and release surfaces, not a claim that every line of all 1,045 entries has received manual review. Historical skills and old feature branches need targeted relevance checks before deletion or promotion.

## Verified positives

- The three active `ai-coding-orchestrator/SKILL.md` entrypoints under `skills/`, `.agents/` and `.claude/` have the same blob SHA on the baseline.
- `VERSION` is `1.0.4`; the existing contract test checks the Claude plugin and marketplace version alignment.
- PR #605's five reported required workflows passed on exact head `2a5f5470685446b766cbc9d0f8dce8dac9ea21ed` before merge as `401902f24fa87a968300d96be095bdc96e78fad8`. Post-merge push-run evidence was not available in this review response, so it is not asserted here.
- Cleanup governance protects the README, Pages entrypoints, deployment workflow, portable bundle and rollback documentation.
- The Independent Generalization Arena contract correctly states that repository tests are not independent external evaluation and that Arena evidence must not grant execution authority.

## Findings and required outcomes

| Priority | Finding | Current status | Completion evidence |
|---|---|---|---|
| P0 | Runtime state root mismatch: an earlier revision used the legacy `~/.aer` root as the active default and could nest `.auren` unexpectedly. | Canonical default paths now use `~/.auren`; `~/.aer` remains only as a guarded migration source. Keep [#379](https://github.com/sourabhJainR/AUREN/issues/379) open until exact-head tests and live migration/rollback evidence are confirmed. | One canonical path contract, safe migration/compatibility logic, collision and restart tests, rollback evidence. |
| P0 | `EngineeringRecoveryLedger` is now integrated into `AdaptiveRuntime.run()` for bounded attempts, alternate hypotheses, and durable checkpoints. | [#377](https://github.com/sourabhJainR/AUREN/issues/377) remains open for process-death restart/resume, dependency-aware continuation, and exact-head/last-known-good operational evidence. | Integration tests must prove restart/resume and bounded recovery through the default runtime; operator/production evidence remains separate. |
| P0 | There are two different `StateGraph` classes: `portable.state_graph.StateGraph` is a supervisor-facing facade over `Graph/Orchestrator`; `portable.agency_state_graph.StateGraph` is a separate checkpointable graph runtime. Documentation had incorrectly claimed no `StateGraph` class existed. | README and controller docs corrected in this review PR; further runtime unification is not assumed safe. | Explicit API ownership, adapters where needed, and tests showing which path the default runtime actually uses. |
| P0 | The Arena is still the main product evidence gap: target contracts exist, but the first fully independent sealed campaign is not yet demonstrated. | Existing [#368](https://github.com/sourabhJainR/AUREN/issues/368) is the canonical roadmap. | Executable sealed runner, independent oracle, content-addressed receipt, holdout controls, repeatability and negative tests. |
| P1 | GitHub Pages publishing has since been reported working and the README points to the published site. | Keep [#380](https://github.com/sourabhJainR/AUREN/issues/380) open only for any remaining externally verified deployment/homepage/404 acceptance evidence; do not repeat the earlier `has_pages=false` observation as current state. | Record a successful deployment and externally observed homepage/404 checks if not already captured. |
| P1 | The canonical identity audit and machine-readable legacy-name inventory now exist, but the broader identity cleanup remains incomplete. | [#530](https://github.com/sourabhJainR/AUREN/issues/530) stays open for review of remaining active tracked matches; preserve explicit migration aliases and historical fixtures instead of blind global replacement. | Run `python scripts/validate_auren_identity.py --report-json <outside-repo-path>`, review each remaining match, and add narrow regression tests for any approved exceptions. |
| P1 | Several validation/fix branches are stale and diverged from main; some contain useful tests absent from main. They should not be merged wholesale. | Not merged by this review. | Inspect each test against current APIs, port useful regression cases onto a fresh branch, and run current CI. |

## Enhancement completion sequence

### Gate 0 — Consistency and truthfulness (this PR)

- Align the Arena and skills-integration documents with the AUREN identity and canonical release version.
- Describe both graph APIs accurately; do not conflate the facade and lower-level runtime.
- Add regression tests for the documentation/API contract.
- Preserve the last-known-good main commit and use normal PR/CI/rollback controls.

**Exit gate:** exact-head CI green; no new runtime behavior change hidden inside documentation cleanup.

### Gate 1 — State ownership and migration ([#379](https://github.com/sourabhJainR/AUREN/issues/379))

- Define machine-scoped vs project-scoped state explicitly.
- Introduce one path resolver and a tested migration strategy for legacy `.aer` data.
- If both old and new roots exist, never silently overwrite or shadow one; preserve both and require a deterministic safe resolution.
- Test memory, scheduler, session, execution ledger, restart/resume, installation, update and rollback using isolated temporary home directories.

**Exit gate:** one canonical default path; no lost or silently ignored state; rollback demonstrated.

### Gate 2 — Real bounded recovery integration ([#377](https://github.com/sourabhJainR/AUREN/issues/377))

- Choose one authoritative recovery ledger or provide an explicit adapter to the existing supervisor ledger.
- Connect real failure classification, hypothesis identity, attempt/time budgets, checkpoints and exact-head CI evidence to the default runtime.
- Prove restart/resume and alternate-plan behavior; unrelated safe tasks continue when a dependency is blocked.
- Keep authorization, approval, provenance and rollback gates authoritative.

**Exit gate:** integration tests exercise a real default-runtime task and show that repeated failures terminate or change strategy instead of looping.

### Gate 3 — Arena execution kernel and sealed holdouts ([#368](https://github.com/sourabhJainR/AUREN/issues/368), P0)

- Define canonical benchmark, case, oracle, evaluator, run, result and receipt schemas.
- Execute independently supplied corpus material through a separate evaluator boundary.
- Bind the receipt to corpus, runtime and evaluator digests; make replay, contamination, malformed evidence and missing identity fail closed.
- Ensure the evaluated runtime cannot inspect or mutate sealed holdout material and cannot choose its own oracle.

**Exit gate:** deterministic repeat runs; negative/adversarial tests; zero runtime write/authority path into evaluator or holdout; complete receipt lineage.

### Gate 4 — Transfer, attestation and evidence-to-learning (P1)

- Add domain adapters and score unseen task families/environments separately from source-domain regression.
- Separate zero-shot, few-shot and adapted performance.
- Bind external attestation to receipt digest, evaluator identity and freshness; reject self-attestation where independence is required.
- Feed only accepted immutable observations to causal attribution and curriculum selection. Arena and Learning remain advisory; Guard alone authorizes promotion/rollback.

**Exit gate:** independent target-domain evidence can be reproduced and audited without granting evaluation or learning authority.

### Gate 5 — First independent campaign (P2)

Run one small, genuinely independent campaign with an unseen task family, unseen project/environment, sealed holdout, independent oracle, external receipt retention, deterministic repeat, and adversarial negative cases. Publish measured results and limitations; do not label this AGI proof.

**Exit gate:** the full receipt and attestation chain is independently verifiable and the results can be reproduced by a party outside the evaluated runtime.

### Gate 6 — Public release and regression proof

- Resolve [#380](https://github.com/sourabhJainR/AUREN) and verify the live Pages URL.
- Port useful tests from stale validation branches only after confirming they still describe current APIs.
- For every phase: implement a small slice → open PR → verify exact-head CI → fix/review → merge → rerun critical checks on merged main → record evidence → proceed to the next phase.
- Retain a concrete revert/data-restore path before any cleanup, state migration, or release change.

## Operating rule

Do not equate documentation, a helper module, a passing unit test, or a green CI workflow with end-to-end capability. A phase is complete only when the actual default path demonstrates the acceptance criteria and the evidence is tied to the exact tested revision.


## Execution evidence update — 2026-10-11

This section supersedes earlier "pending implementation" statements only where the specific merged change and exact-head CI evidence below support that status. It does not waive external or production-environment acceptance gates.

### Completed implementation and exact-head verification

| Workstream | Evidence | Status / remaining acceptance |
|---|---|---|
| Architecture consistency | [PR #381](https://github.com/sourabhJainR/AUREN/pull/381) merged after its required workflows passed; merge baseline is [7dc0553](https://github.com/sourabhJainR/AUREN/commit/7dc055383c30ee359bde75145f90262f5e691b5f). | Documentation/API ownership correction is merged. Continue protecting the documented graph API distinction. |
| State path + default runtime recovery | [PR #382](https://github.com/sourabhJainR/AUREN/pull/382), exact head [0084263](https://github.com/sourabhJainR/AUREN/commit/00842634ac1d9e47156be0b0cd1aee85f6c58aac); merged as [4cc1f54](https://github.com/sourabhJainR/AUREN/commit/4cc1f548c769e0ed3c03917d0f87df992a23a7ab). Exact-head runs passed: AI Harness Check #4705, Production AI Harness Hardening #1625, Architecture Integrity #1198, AUREN Portable Bundle #1753, Agent Capability Runtime #1050. | Automated suite exercised migration through `AdaptiveRuntime`, three bounded attempts (`default-plan`, same-hypothesis retry, then `alternate-plan-1`), durable checkpoint contents, and recovery persistence after constructing a fresh runtime. State path unit tests cover legacy SQLite/session copying, preservation, idempotence and conflicting roots. Still require a controlled upgrade/rollback rehearsal against representative real legacy stores and explicit interrupted-migration/restart-resume evidence before closing #379; #377 remains open until exact-head CI gating is demonstrated in the actual engineering controller, not merely as a helper or CI workflow. |
| Sealed Arena boundary hardening | [PR #383](https://github.com/sourabhJainR/AUREN/pull/383), exact head [3fb6417](https://github.com/sourabhJainR/AUREN/commit/3fb64179a5b6e2bd7a2c9111236b4ba6b0cbd519); merged as [c426a23](https://github.com/sourabhJainR/AUREN/commit/c426a2318535b98d6f905b1f8700c077b9485d0f). Exact-head runs passed: AI Harness Check #4697, Production AI Harness Hardening #1621, Architecture Integrity #1194, AUREN Portable Bundle #1749. | Negative tests cover malformed schema, path containment, nested answer leakage, incomplete receipts and unassigned domain cases. This is protocol hardening only—not proof of external generalization. |
| Merged main revision | The repository commit search after both merges returned [4cc1f54](https://github.com/sourabhJainR/AUREN/commit/4cc1f548c769e0ed3c03917d0f87df992a23a7ab) as the latest commit, and the default-branch runtime source contains the terminal recovery checkpoint fix. | Merge identity and merged source content verified. The available connected Actions query returns PR-triggered workflow runs only; it does not expose the post-merge push-run list, so do not represent post-merge push CI as independently confirmed by that query. Re-check Actions on the current default-branch revision after this plan update. |

### Explicit external / repository-administrator blockers

1. **Independent Arena campaign — #368 remains open.** No genuinely external evaluator and oracle, independently controlled unseen benchmark/holdout, retained receipt/attestation, or outside-party reproduction has been supplied. Do not substitute local tests or self-attestation. Acceptance requires an independent party to execute a sealed campaign, publish the corpus/runtime/evaluator digests and signed/content-addressed receipt, repeat the measurement, and document limitations and negative controls.
2. **GitHub Pages publication — #380 remains open.** The deployment workflow and compatibility entrypoints exist, but the live site could not be verified from the available external fetch, and the connected GitHub actions do not expose the repository Pages-settings mutation. A repository administrator must enable Pages with GitHub Actions, run deployment from main, and verify both the canonical homepage and a missing-path 404 against the deployed commit.
3. **State migration rollout — #379 remains open.** Code and automated tests are merged, but production rollout acceptance still requires a backed-up, representative legacy-state rehearsal; confirmation that both-root conflicts fail without loss; interruption/restart-resume checks; and a demonstrated rollback/data-restore path.
4. **Runtime recovery gate — #377 remains open.** Bounded retries, alternate-plan switching and durable checkpoints are exercised in the default runtime. Remaining acceptance is wiring exact-head CI evidence into the real engineering promotion/controller path, proving stale/running/skipped checks fail closed, and testing restart/resume and safe termination under the integrated controller.

### Next acceptance sequence

- [ ] Re-check required push-triggered workflows on the current default-branch SHA after this plan change; repair failures before claiming main is green.
- [ ] Run and retain a controlled state migration + rollback rehearsal using a disposable copy of representative legacy SQLite/session state.
- [ ] Integrate and test exact-head CI evidence as a hard gate in the real engineering controller; reject stale, incomplete, skipped or non-successful runs.
- [ ] Obtain an external evaluator/oracle and complete the first independent sealed Arena campaign; keep #368 open until receipt and measurements are independently reproducible.
- [ ] Obtain repository administrator action to enable Pages; verify homepage and 404 from the deployed commit before closing #380.
- [ ] Close #379, #377, #368 or #380 only when their specific acceptance evidence is attached to the corresponding issue.

**Production-readiness statement:** the two implementation PRs are merged and their listed exact-head workflows passed. AUREN is not yet fully production-accepted for this enhancement program while the external Arena campaign, Pages publication, real-store migration/rollback rehearsal and controller-enforced exact-head CI gate remain outstanding.


## Current-main re-review — 2026-10-13

**Review baseline:** `492799ee7237ba690a3ce23db2012997855d1cb3`. This section supersedes older status statements above where later code and issue updates provide newer evidence. Historical findings are retained for provenance.

### Revalidated state and recovery work

- `portable.adaptive_runtime.AdaptiveRuntime` now resolves the canonical machine state root through `AurenStatePaths` at `~/.auren`, and passes the legacy `~/.auren` root through the guarded migration path. The earlier table statement that the current runtime still initializes all stores under `~/.auren` is stale.
- `AdaptiveRuntime.run()` now directly uses `EngineeringRecoveryLedger` for bounded attempts, alternate hypotheses, and checkpoints. The earlier statement that the default runtime does not use the ledger is stale.
- Issues [#379](https://github.com/sourabhJainR/AUREN/issues/379) and [#377](https://github.com/sourabhJainR/AUREN/issues/377) remain open for live/operational acceptance: representative legacy-state upgrade and rollback rehearsal, process-death restart/resume, unrelated work proceeding around blocked dependencies, and exact-head CI/last-known-good evidence. Do not close them based only on unit tests.

### Newly confirmed graph/crew consistency gap

The graph APIs and team strategy are not yet one integrated dispatch path:

1. `portable.adaptive_runtime.AdaptiveRuntime` composes the default task run through `portable.orchestration.Graph/Orchestrator`.
2. `portable.state_graph.StateGraph` is a supervisor-facing facade over that orchestration API.
3. `portable.agency_state_graph.StateGraph` is a separate checkpointable graph kernel used by `.ai-harness/runtime/graph_agent_team.py`.
4. `portable.crew_adapters` and `portable.state_graph.GraphAgentTeam` implement typed crew selection and scoped proposal dispatch. The harness graph now imports shared plan/proposal helpers and dispatches registered adapters in a graph-owned node; provider-specific adapters are host-registered and are not configured by default.
5. `README.md`, `docs/ARCHITECTURE_CONSTITUTION.md`, `architecture/compatibility.yaml`, and pattern-adaptation documents had described overlapping owners inconsistently.

The current owner map is documented in [`docs/UNIFIED_GRAPH_CREW_RUNTIME.md`](UNIFIED_GRAPH_CREW_RUNTIME.md). The harness now has a graph-owned proposal-dispatch path, but default provider adapters are not preconfigured. Issue [#438](https://github.com/sourabhJainR/AUREN/issues/438) remains the acceptance gate for durable cross-crew handoffs, one retry budget across all layers, and full end-to-end evidence/restart/rollback proof.

### Documentation drift found

The current-main search also found active AUREN-era naming and paths in console, orchestration, agent-capability, provider, second-brain, learning, offload, and adaptation documents. This PR aligns the active graph/crew contract surfaces and selected runtime docs, fixes the console commands and canonical state path, and adds regression checks for the changed active docs. Historical/version-specific notes and explicit legacy identifiers (for example, the `.auren` migration source or `AER_*` compatibility environment variables) must remain labeled as legacy rather than being blindly renamed.

### Next acceptance sequence

- [x] Integrate shared crew planning and proposal validation into a graph-owned harness node; retain the graph as the only dependency and execution authority.
- [ ] Register provider-specific adapters at trusted host bootstrap and prove that required high-risk phases fail closed when an adapter is absent.
- [ ] Prove deterministic crew assignment and stable digest binding across graph, intent, crew contract, and adapter configuration.
- [ ] Test independent read-only parallel work, serialized mutations, durable cross-crew handoffs, missing adapter behavior, blocked dependencies, a single retry budget, checkpoint/resume, independent QA, and unauthorized promotion attempts.
- [ ] Consolidate retry ownership so node retries, crew handoff retries, and supervisor recovery consume one bounded task budget.
- [ ] Verify memory/evidence ownership: task-scoped memory remains a projection, not a second durable source of truth.
- [ ] Run identity/path consistency tests, architecture validation, full test suite, exact-head CI, and post-merge main re-evaluation.
- [ ] Continue the separate independent Arena, external attestation, and operator migration/rollback gates; repository CI cannot substitute for them.


## Current status refresh — 2026-10-16

This section supersedes older open/closed issue statements in the historical review sections above. Those sections remain as dated evidence of what was known at their review baselines; do not use their former issue states as the live status.

| Area | Current repository status | Remaining acceptance |
|---|---|---|
| Canonical runtime state and migration code | Implementation issue [#379](https://github.com/sourabhJainR/AUREN/issues/379) closed on 2026-10-15 after code and regression coverage were merged. The canonical default state root is `~/.auren`; legacy data is preserved and conflicting roots fail closed. | Operator-controlled staging migration, restart/resume, reconciliation, and rollback evidence remains open in [#534](https://github.com/sourabhJainR/AUREN/issues/534). Do not delete either state root before operator acceptance. |
| Bounded recovery | Issue [#377](https://github.com/sourabhJainR/AUREN/issues/377) closed on 2026-10-15 after runtime integration and recovery regression work. | Continue validating the current default path and exact-revision CI gates; do not equate historical helper tests with live production evidence. |
| Specialist handoffs | Issue [#438](https://github.com/sourabhJainR/AUREN/issues/438) closed on 2026-10-15 after PRs [#658](https://github.com/sourabhJainR/AUREN/pull/658) and [#661](https://github.com/sourabhJainR/AUREN/pull/661). Repository tests exercise supervisor-owned persistence, restart/resume, idempotency, independent review, and aligned retry-budget digests. | Provider-specific live adapters remain host-registered and are not configured by default. The portable and harness graph kernels remain distinct; do not claim full graph-kernel unification. |
| Sandbox-isolation attestation | Issue [#471](https://github.com/sourabhJainR/AUREN/issues/471) closed on 2026-10-15 after the signed-attestation evidence gate and regression tests were implemented. | A valid signature authenticates a report; it does not independently prove the external operator's isolation claims. Independent external audit evidence remains a separate requirement. |
| Identity cleanup | Issue [#530](https://github.com/sourabhJainR/AUREN/issues/530) closed on 2026-10-15 after the AUREN-era naming/path audit and documentation reconciliation. | Preserve explicit legacy migration aliases and historical fixtures; do not perform blind global renames. |
| GitHub Pages | Issue [#380](https://github.com/sourabhJainR/AUREN/issues/380) closed on 2026-10-13 after Pages publication restoration. The deployment workflow publishes `site/`. | Verify the exact merged commit's deployment run and public content after each material site change; repository CI alone is not proof that the public page has refreshed. |
| Independent generalization | Issue [#368](https://github.com/sourabhJainR/AUREN/issues/368) closed after the executable Arena contract/hardening work. | The first independently operated external campaign remains open in [#404](https://github.com/sourabhJainR/AUREN/issues/404), including independent evaluator/oracle, unseen corpus, sealed holdout, repeat run, adversarial cases, and retained attestation. |

The 20 AI-era foundation principles are registered in `portable/ai_era_principles.py`, documented in `docs/AI_ERA_FOUNDATION_PRINCIPLES.md`, and referenced from `AGENTS.md` and `ENGINEERING_PRINCIPLES.md`. The orchestrator records a digest-bound receipt before graph execution. The receipt is traceability evidence, not proof of universal enforcement; each control's owning module, regression coverage, and external acceptance boundary remain authoritative.
