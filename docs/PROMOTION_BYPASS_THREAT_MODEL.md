# Promotion bypass threat model and required deployment controls

## Default runtime enforcement

The durable `ExecutionController.run_once` path claims a task, obtains promotion
context from a registered trusted provider without passing task payload, validates
the decision record against live repository/head context, checks canonical evidence
and exact-head CI, and invokes the handler only after preflight passes. Gate failures
are retained as failed execution evidence. Handler re-registration cannot silently
erase persisted CI or promotion policies.

The registration guard checks both the registered handler name and importable
callable identity so a routine alias cannot trivially hide a `merge`, `deploy`,
`publish`, or promotion handler. Regression tests cover missing/stale decision
records, task-payload spoofing, unknown evidence IDs, unauthorized approvers,
missing rollback action/anchor, skipped checks, in-progress CI, wrong CI head,
missing providers, re-registration, and innocuously named aliases.

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

Name-based classification is a safety net, not a proof that arbitrary Python
handlers are side-effect free. A handler can hide a privileged operation behind a
neutral name, or code can call a privileged external API outside AUREN. Python
in-process checks cannot prevent a process holding unrestricted repository or
cloud credentials from using them directly.

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
