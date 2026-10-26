# P0 extension execution and recovery gates

## Optional extension execution contract

Discovery metadata never grants execution authority. Any optional tool registered with `ToolSpec(is_extension=True)` must pass both the normal tool allowlist and the extension policy gate before its handler runs. Missing metadata, invalid manifests, absent trust, missing task approval, undeclared permissions, stale artifacts, absent output limits, and unauthorized provider IDs deny before dispatch. MCP uses the same `ToolRegistry.execute` boundary.

Command-backed providers that are optional extensions must be created with `is_extension=True` and supply the explicit policy context. The provider gate runs before subprocess creation, verifies that the configured provider ID is included in the policy's `provider_ids`, and caps returned text. The subprocess byte limit remains an independent lower-level bound. A caller-supplied freshness assertion is insufficient unless the policy metadata also says `artifact_fresh=true`.

The extension flag is an explicit registration contract: core tools and core providers remain on their existing path; extension adapters must not omit the flag or route around the registry. Any new extension execution adapter must include a denial-before-execution test for absent policy and a bounded-output test.

## Recovery and side-effect fault matrix

The recovery suite exercises process death after the durable claim, provider acceptance before receipt persistence, and provider completion before local completion persistence. It verifies reconciliation, repeated restart, immutable idempotency payloads, and no redispatch while the remote outcome is ambiguous. The provider lifecycle and execution-controller suites additionally cover receipt-write failures, verifier exceptions, concurrent reconciliation, task cancellation, bounded retry budgets, checkpoint tampering, real process restart, and unknown provider outcomes.

The invariant is conservative: an interrupted consequential operation is `reconcile_required`, not a fresh execution. Only a trusted reconciliation receipt or explicit evidence-backed no-effect retry authorization may move it forward. Missing evidence, verifier exceptions, stale checkpoints, or budget exhaustion cannot be interpreted as success.

## Validation and rollback

Run the extension policy, tool/MCP, subprocess provider, side-effect ledger, provider reconciliation, provider lifecycle, execution-controller, retry-budget, and recovery-journal tests, followed by applicable architecture, hardening, resilience, and portable-bundle workflows on the exact PR head. If policy changes cause unexpected denials or recovery tests reveal ambiguous state being retried, do not merge; revert the specific change and retain the fail-closed behavior.
