# AUREN Repository Consistency Review and Enhancement Completion Plan

**Review baseline:** `ec198bdf267c1c32de2c4cc97d0bbee8e35cebd7`  
**Review date:** 2026-10-11  
**Purpose:** record evidence-backed consistency findings, distinguish verified implementation from intended architecture, and order the remaining work without duplicating existing control planes.

## Scope and confidence

The review inventoried the complete GitHub tree (1,045 entries), default branch, release metadata, workflow configuration and recent Actions runs. It then inspected the canonical agent instructions and skill mirrors, graph APIs, adaptive runtime, durable execution controller, autonomous supervisor, recovery ledger, architecture/arena documentation, and the related tests/issues.

This is a deep review of the main architectural and release surfaces, not a claim that every line of all 1,045 entries has received manual review. Historical skills and old feature branches need targeted relevance checks before deletion or promotion.

## Verified positives

- The three active `ai-coding-orchestrator/SKILL.md` entrypoints under `skills/`, `.agents/` and `.claude/` have the same blob SHA on the baseline.
- `VERSION` is `1.0.4`; the existing contract test checks the Claude plugin and marketplace version alignment.
- All seven push-triggered workflows shown for merged commit `ec198bdf267c1c32de2c4cc97d0bbee8e35cebd7` completed successfully.
- Cleanup governance protects the README, Pages entrypoints, deployment workflow, portable bundle and rollback documentation.
- The Independent Generalization Arena contract correctly states that repository tests are not independent external evaluation and that Arena evidence must not grant execution authority.

## Findings and required outcomes

| Priority | Finding | Current status | Completion evidence |
|---|---|---|---|
| P0 | Runtime state root mismatch: README documents `~/.auren`; `portable/adaptive_runtime.py` still uses `~/.aer`, and passes `~/.aer/executive` to a controller that appends its own `.auren` directory. | Tracked in [#379](https://github.com/sourabhJainR/AUREN/issues/379). Do not move data manually. | One canonical path contract, safe migration/compatibility logic, collision and restart tests, rollback evidence. |
| P0 | `EngineeringRecoveryLedger` is implemented and unit-tested, but the reviewed default `AdaptiveRuntime → ExecutionController → AutonomousCompany` path does not directly use it. Existing supervisor retries and the new ledger could otherwise enforce separate budgets. | [#377](https://github.com/sourabhJainR/AUREN/issues/377) remains open pending real runtime integration. | Integration tests prove durable attempt limits, changed-hypothesis recovery, exact-head CI gates, resume and safe termination through the default runtime. |
| P0 | There are two different `StateGraph` classes: `portable.state_graph.StateGraph` is a supervisor-facing facade over `Graph/Orchestrator`; `portable.agency_state_graph.StateGraph` is a separate checkpointable graph runtime. Documentation had incorrectly claimed no `StateGraph` class existed. | README and controller docs corrected in this review PR; further runtime unification is not assumed safe. | Explicit API ownership, adapters where needed, and tests showing which path the default runtime actually uses. |
| P0 | The Arena is still the main product evidence gap: target contracts exist, but the first fully independent sealed campaign is not yet demonstrated. | Existing [#368](https://github.com/sourabhJainR/AUREN/issues/368) is the canonical roadmap. | Executable sealed runner, independent oracle, content-addressed receipt, holdout controls, repeatability and negative tests. |
| P1 | GitHub repository metadata reports Pages disabled (`has_pages=false`) despite a configured Actions deployment workflow and homepage URL. | Tracked in [#380](https://github.com/sourabhJainR/AUREN/issues/380); repository settings must be enabled and the live URL verified. | Successful Pages deployment plus external homepage and 404 smoke checks. |
| P1 | Several active docs and source comments still use the retired AER identity; one integration note also claims a separate `21.0.0 → 22.0.0` version jump that conflicts with the canonical `VERSION` contract. | The Arena and skills-integration docs and selected active comments are corrected in this review PR; regression checks prevent these specific docs from drifting again. | Extend identity checks to additional maintained surfaces, preserving only explicitly documented compatibility identifiers. |
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
