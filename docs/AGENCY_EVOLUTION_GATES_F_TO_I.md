# AUREN agency evolution Gates F-I

## Gate F — Runtime integration and durable state

`portable/agency_evolution_runtime.py` coordinates experiment state in SQLite with WAL and full synchronous durability. It pins each experiment to the immutable manifest digest and derives a stable operation ID. Reusing an experiment ID with a different manifest is a conflict. SQLite's write transaction serializes local claim/update operations.

On process restart, a running operation is not silently re-dispatched. Recovery marks its remote outcome unknown and requires reconciliation against the trusted controller/provider. A retry becomes eligible only when reconciliation evidence explicitly establishes that no side effect started. A reported remote completion remains untrusted until evaluation evidence is attached. Retry attempts and cycle count are finite.

The runtime can ingest trace metadata and prioritize source-linked signals. Signals are leads for investigation, not ground truth. The existing `ImprovementJournal` can receive queued-operation receipts; the runtime also keeps a database event chain and verifies its digests.

## Gate G — Experiments and independent evaluation

Each experiment requires an `ExperimentManifest` with baseline/candidate revisions, dataset digest, evaluator revision, seed, metrics and finite budget. Sealed data can only be referenced by ID; it cannot be embedded in the manifest. The result API requires an explicit outcome and evidence references. `unknown`, `unscored`, and `inconclusive` never count as a pass. A passing outcome is marked verified only when independence and holdout isolation are explicitly asserted. The runtime requires accepted evaluator calibration against independently adjudicated cases before promotion, and records both false-pass rate and accuracy. Calibration assertions and references are not cryptographic attestations; the deployment must source them from trusted evaluator/custody records.

Evaluation summaries are bounded and reject prompt, response, payload, secret, token, credential and holdout-data fields. Per-domain scorecards are exposed by `snapshot()`. Drift assessments compare observations to both a fixed release baseline and an adaptive operational baseline; either detected drift blocks promotion. These are bounded mean/tolerance checks, not full distribution-shift detectors. Candidate promotion still requires the Gate D evidence contract and successful regression/risk gates.

## Gate H — Controlled release and rollback

This module records release lifecycle evidence; it does **not** deploy, promote, or roll back software itself. Every release event must be attributed to an authorized trusted controller and reference evidence. The lifecycle enforces verified shadow evidence, verified approval before canary, verified canary before promotion, and a recorded rollback start before rollback verification. The trusted controller must execute and independently verify those actions, restore the pinned revision on rollback, and check runtime health. A boolean passed by an untrusted caller is not proof of authority.

The existing execution controller remains the authority boundary. Do not wire a direct provider callback or self-approval into this module. Ambiguous remote effects must use the existing side-effect ledger/provider lifecycle reconciliation contract.

## Gate I — Observability and bounded cycles

`snapshot(operation_id)` exposes durable state, outcome, attempts, evidence references, event count/digests, per-domain metrics, release authority boundary, and a concrete next action. The event chain can be verified after restart. A follow-on cycle is accepted only after its parent is completed with a verified passing outcome, and the cycle budget is enforced. This supports auditable two-cycle tests without allowing the learner to approve itself.

## Validation and rollout

Run focused tests:

```bash
python -m unittest tests.test_agency_evolution_gates tests.test_agency_evolution_runtime -v
python -m unittest tests.test_execution_controller_restart tests.test_provider_operation_lifecycle -v
```

The production-hardening and runtime-resilience impact policies include the runtime and focused tests. CI must run on the exact PR head; a required check that is skipped is not a pass. Also run supported Windows checks for the repository's required matrix.

### Current boundary / remaining deployment evidence

- The runtime is a durable coordinator, not a complete provider executor. Actual provider side effects remain behind the trusted execution controller and its idempotency/reconciliation ledger.
- Evaluator independence, sealed-holdout custody, authority, and signatures must be verified by trusted external services; this module records their evidence references and explicit results but cannot manufacture trust.
- The module does not claim statistically validated unseen-domain transfer or distribution-sensitive drift detection. Use independent Arena/holdout campaigns and fixed-release-baseline checks before promotion.
- Two consecutive cycles are covered by tests, not by a claim that a live production deployment has completed them.
- Before rollout, rehearse process interruption, unknown provider outcomes, failed canary, rollback execution, and restored-revision health checks. Preserve the event database and evidence references on rollback.

## Rollback

Disable callers that instantiate this coordinator, then revert the runtime and test commit(s). Do not delete the SQLite database or the improvement journal as part of rollback; preserve them for investigation. Since the module does not dispatch providers or promote releases itself, reverting it does not undo a remote side effect. The trusted controller must reconcile or roll back any in-flight external operation before work resumes.
