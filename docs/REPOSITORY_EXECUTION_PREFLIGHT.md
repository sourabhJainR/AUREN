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
3. Discover the actual tool schema and validate required fields, types, enum
   values, bounds and unexpected arguments before invoking the tool.
4. Record capability checks with evidence and timestamp. Missing capabilities
   are blockers, not passes.
5. Map each acceptance requirement to implementation, positive test, negative
   test, integration/browser test and evidence. A passed row must carry evidence
   for the exact current full commit SHA.
6. Poll CI to terminal state and run `exact_head_ci_gate` against the current
   SHA. A stale SHA, pending/unknown result, failed check or skipped required
   check blocks acceptance. Skipped non-required checks remain explicitly
   skipped. The caller must supply the expected required-check names so missing
   checks cannot disappear from the response.
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
that cannot be mapped to a GitHub owner/repository is blocked by default. An
operator who has reviewed the target may explicitly permit this local-only case
with \`AUREN_ALLOW_UNVERIFIED_REPOSITORY_IDENTITY=1\`; this does not bypass the
separate governance-repository opt-in for a known AUREN remote. It covers the
Vibe Coding host entry point; it does not automatically govern host-native
tools or every external GitHub client.
