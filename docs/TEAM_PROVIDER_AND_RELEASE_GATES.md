# Provider, workspace and release acceptance gates

The requirements-to-release path uses the existing supervisor, ExecutionController,
versioned profiles, shared team budget, Guard and evidence ledger. These new helpers
are gate adapters, not a second orchestrator or authority store.

## Provider and licensing readiness

portable.provider_readiness.evaluate_provider_readiness requires two separate
host-authenticated receipts for each provider/capability used by a release:

- A fresh live probe bound to a host-generated challenge, exact provider ID,
  capability, adapter version and task/run/candidate digests.
- A current license review bound to the exact provider and intended use, with a
  source URL, terms digest, reviewer identity, expiry, permitted uses, and explicit
  redistribution, commercial-use and storage rights.

The host must supply verifiers that authenticate the provider probe and the
license review. Missing, stale, expired, untrusted, mismatched or insufficient
evidence never passes. Static manifests and deterministic fixtures do not count
as live validation. The report contains digests and status, not credentials or
raw provider responses.

## Independent review and release authorization

portable.team_release_authorization.authorize_team_release requires:

- Canonical supervisor readiness for the same task and run.
- Provider/license reports for the exact artifact, commit and plan revision.
- A host-authenticated workspace isolation receipt for that exact candidate.
- Separate signed QA (QA-01) and security (SEC-01) approvals.
- A separate release approver, configured by the trusted host.
- Distinct QA, security and release principals, none of whom may be an implementer.
- A host callback that persists the authorization into the canonical durable
  approval/release ledger and returns the committed record ID and digest.

The authorization helper does not deploy software. The host must call it at the
actual promotion/deployment boundary and deny that action unless it returns
authorized=true. Human/operator approval remains required where deployment
policy says so.

## Workspace boundary

A Git worktree provides separate source trees and conflict-safe proposal adoption.
It is not proof of OS isolation. The workspace receipt verifier must validate a
host-enforced sandbox or container boundary, including filesystem scope, network
egress, credential isolation, process termination and cleanup. If the host cannot
produce that receipt, release remains blocked.

## Validation

Run:

python -m unittest tests.test_provider_readiness tests.test_team_release_authorization

These tests exercise deterministic contract fixtures only. They do not prove that a
provider account, current license terms, sandbox host, independent reviewers, or
release ledger are configured in a real deployment.
