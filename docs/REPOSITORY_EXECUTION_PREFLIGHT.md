# Repository execution preflight and acceptance evidence

`portable.repository_execution_preflight` adds deterministic contracts for the
AUREN-governed implementation workflow. It complements the canonical execution
controller, Guard, `ExecutionEnvelope`, `EngineeringRecoveryLedger`, and
`CanonicalEvidenceLedger`; it is not a second orchestrator or authority store.

## Required sequence

1. Declare implementation repository, destination branch, full base SHA,
   allowed write paths, governance repository and (if separate) evidence repo.
2. Compare the declaration with the observed checkout/remote before writes.
   Product implementation in the governance repository is rejected unless the
   plan explicitly opts in.
3. Discover the actual tool schema and recursively validate required fields, types,
   enum values, object properties, array items, bounds and unexpected arguments
   before invoking the tool. Unsupported schema keywords fail closed.
4. Record capability checks with evidence and timestamp. Missing capabilities
   are blockers, not passes.
5. Map each acceptance requirement to implementation, positive test, negative
   test, integration/browser test and evidence. A passed row must carry evidence
   for the exact current full commit SHA.
6. Poll CI to terminal state and run `exact_head_ci_gate` against the current
   SHA. A stale SHA, pending/unknown result, failed check or skipped required
   check blocks acceptance. Skipped non-required checks remain explicitly
   skipped. The caller must supply the expected required-check names so missing
   checks cannot disappear from the response. An empty required-check list is rejected.
7. Persist a fully passed matrix through the existing `CanonicalEvidenceLedger`.
   Its local digest is tamper-evident against accidental changes, not a digital
   signature or independent custody proof.

## API sketch

```python
target = RepositoryTarget(
    repository="owner/product",
    branch="feature/change",
    base_sha="<full 40-character SHA>",
    allowed_paths=("src", "tests/**"),
    governance_repository="owner/auren",
)
target.validate_observed_target(
    repository=observed_repo, branch=observed_branch, base_sha=observed_sha
)
target.authorize_path("src/module.py")
validate_tool_arguments(discovered_schema, tool_arguments)
blockers = capability_blockers(observed_capabilities, required_capabilities)
gate = exact_head_ci_gate(current_head_sha, workflow_checks,
                          required_check_names=required_checks)
```

## Boundaries and remaining external proof

The contracts do not discover GitHub schemas or execute commands themselves;
the caller must supply schemas and capability evidence from the actual host.
Path checks are a plan-scope guard, not a filesystem sandbox; the I/O adapter
must still resolve symlinks and enforce canonical roots. CI results must be
fetched from the provider and polled by the existing recovery/controller path.
A passing local matrix does not prove branch protection, independent review,
production browser behavior, deployment, or external evaluator custody.

## Regression

```bash
python -m unittest tests.test_repository_execution_preflight -v
python -m unittest tests.test_execution_boundary tests.test_execution_contract tests.test_quality_optimizer_contract tests.test_evaluation_rubric -v
```

## Active Vibe Coding host protection

Before opening an isolated worktree, `LocalVibeCodingHost` checks the
project's GitHub `origin` against the governance repository. The default
governance slug is `sourabhJainR/AUREN`; deployments can set
`AUREN_GOVERNANCE_REPOSITORY=owner/repo` to override it. A match blocks
workspace writes unless the operator explicitly sets
`AUREN_ALLOW_GOVERNANCE_REPOSITORY_CHANGES=1`. This keeps product work in
its product repository while allowing deliberate AUREN self-maintenance.

The check is performed before worktree creation. A missing origin or an origin
that cannot be mapped to a GitHub owner/repository is blocked by default. For a
local or non-GitHub target, the operator must explicitly set
\`AUREN_ALLOW_UNVERIFIED_REPOSITORY_IDENTITY=1\` and declare
\`AUREN_IMPLEMENTATION_REPOSITORY=owner/repo\`. The declaration is not remote
proof; it only makes the operator's target explicit. If the declared target is
the governance repository, \`AUREN_ALLOW_GOVERNANCE_REPOSITORY_CHANGES=1\` is
still required. It covers the Vibe Coding host entry point; it does not
automatically govern host-native tools or every external GitHub client.


## Canonical controller feasibility and approval binding

The durable controller reuses `EngineeringDesignGuard.review_request` at plan admission. It evaluates the declared requirements, assumptions, constraints, acceptance criteria and non-goals before a task record is created. Known unresolved contradictions (for example, live quotes versus delayed-only data, offline-only versus required remote API access, or no external retention versus durable history) reject admission before handler execution.

The controller stores a supervisor-owned feasibility receipt beside the immutable plan digest and recomputes it at dispatch. A missing, changed or stale receipt blocks execution. Blind spots remain visible in the receipt; a deterministic review is not a semantic proof that every requirement is feasible. The guard's own limitations must be retained and independent review is still required for high-risk changes.

A plan that explicitly requires human approval or consent for an action cannot be submitted through a read-only, CI-gated or ordinary consequential handler. It must use the existing `approval_gated` handler class with a trusted approval policy and approval-aware checkpoint/resume contract. A caller-supplied string or plan field is not approval. The approval decision must be recorded by the configured trusted approval gateway.

Security and privacy constraints must be stated in the plan, not assumed from a generic request to be "secure". External transfer, retention, deletion, credential use and permission changes need explicit scope and the appropriate existing Guard/approval policy. If the required approval gateway or policy is unavailable, execution blocks. Do not send real customer data or credentials to validation jobs; regression fixtures must be synthetic.

The canonical plan test suite includes deliberately contradictory requirements and a privacy-sensitive transfer without approval. It verifies that no durable task or file side effect is created. The FileOperations workflow uses a pinned public repository revision and a disposable CI workspace; it does not read or upload the operator's local files. Its npm/browser build steps require outbound access to package and browser artifact registries in the GitHub-hosted runner.


A plan-scoped `ContextVar` is an adapter guard, not an operating-system sandbox. The guarded filesystem checks both the approved canonical repository root and the relative path, and plan-scoped raw subprocess entry points fail closed. Arbitrary Python handlers can still bypass these adapters with direct filesystem or network APIs unless they run inside an OS-enforced sandbox. Therefore a plan receipt alone must never be used to claim isolation. Do not run untrusted handlers or handle sensitive data under this adapter-only mode; require a configured sandbox and trusted approval/egress policy first.


The feasibility receipt also carries the existing `EngineeringDesignGuard.recommend_approaches` inventory for both the implementation checkout and the AUREN runtime checkout. It exposes known reusable owners and repository evidence before dispatch. This is a bounded, pattern-based inventory, not proof that no duplicate implementation exists; reviewers must inspect the candidate files and justify a new owner when reuse is not suitable.

Plans mentioning personal, sensitive, confidential or customer data, credentials, secrets, security or privacy must declare an explicit data-handling constraint. A constraint that requires consent or approval binds the task to the trusted `approval_gated` path. Plans that declare `isolated_sandbox` as required are rejected when the host probe cannot confirm that capability. Adapter-only plan scope must not be described as isolation.


When the reuse inventory finds code owners in `portable/` or `.ai-harness/runtime/`, admission requires an explicit `reuse_decision` and a complete list of reviewed candidate paths. A `new_component` decision also requires a written justification. The decision and reviewed paths are part of the immutable plan digest and are revalidated at dispatch. This forces a deliberate reuse decision; it does not replace human code review or prove that every possible duplicate has been discovered.
