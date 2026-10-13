# Promotion bypass threat model and required deployment controls

## Default runtime enforcement

The durable `ExecutionController.run_once` path claims a task, obtains promotion
context from a registered trusted provider without passing task payload, validates
the decision record against live repository/head context, checks canonical evidence
and exact-head CI, and invokes the handler only after preflight passes. Gate failures
are retained as failed execution evidence. Handler re-registration cannot silently
erase persisted CI or promotion policies.

Every registered handler now carries an explicit operator-owned `execution_class`
(`read_only`, `context_provider`, `ci_gated`, or `consequential`) persisted
separately from task payload. Unknown legacy rows fail closed until explicitly
reclassified. Consequential handlers require both decision-record and exact-head CI
gates; CI-gated handlers require exact-head CI. Context/evidence providers are
internal-only and cannot be submitted as ordinary tasks. A handler's effect class
cannot be downgraded by re-registration, and action-like callable identities cannot
claim a read-only class. Regression tests cover missing/stale decision records,
task-payload spoofing, unknown evidence IDs, unauthorized approvers, missing
rollback action/anchor, skipped checks, in-progress CI, wrong CI head, missing
providers, re-registration, innocuous aliases, unclassified legacy rows, provider
task submission, and effect-class downgrade attempts.

## Required tests before P0 closure

- [ ] Every real merge/release/deploy/publish adapter is inventoried and bound to
  the same trusted authorization service.
- [ ] Attempts through every alternate CLI, API, agent crew, restart/resume path,
  retry path, scheduled worker, and direct automation path are blocked when the
  decision record, exact-head CI, approver, canonical evidence, or rollback
  readiness is missing or stale.
- [ ] Negative tests prove there are no handler side effects before authorization.
- [ ] Payloads cannot override repository/head, approver set, evidence IDs, gate
  policy, CI status, or rollback evidence.
- [ ] High/critical-risk promotion fails closed when external evidence export is
  pending, evidence is failed/skipped/unknown, or the rollback rehearsal is absent.
- [ ] Branch protection/rulesets require independent status checks and prohibit
  bypass by bot/agent credentials. No evaluated worker has a direct privileged
  merge token that can bypass the Guard-controlled promotion path.
- [ ] External API logs and provider audit logs are checked for attempted bypasses.

## Residual limits

Explicit effect classification is an operator attestation, not a proof that arbitrary
Python handlers are side-effect free. A malicious or mistaken operator can label a
side-effecting function `read_only`; static Python inspection cannot prove otherwise.
Code can also call a privileged external API outside AUREN. In-process checks cannot
prevent a process holding unrestricted repository or cloud credentials from using
them directly. The actual security boundary must therefore be least-privilege worker
credentials plus an independently protected promotion service/branch ruleset.

Therefore “complete bypass resistance” is not established by unit tests alone.
It requires the deployment to put privileged credentials behind the guarded
promotion adapter, apply branch/environment protection independently, restrict
worker identities, and test every actual consequential path in that deployment.
Do not close issue #411 based solely on repository CI.

## Rollback acceptance

A successful unit or container test is not an operator-tested rollback. Follow
[Operator rollback rehearsal](OPERATOR_ROLLBACK_REHEARSAL.md), obtain a signed
receipt from an independently controlled operator identity, and index it as a
passed/verified `rollback_rehearsal` record bound to the exact candidate SHA.
A successful receipt must bind the target, operator, rollback-plan digest,
last-known-good commit, and recovered commit. The recovered commit must equal the
last-known-good commit. Failed rehearsals remain recorded but cannot authorize
promotion.
