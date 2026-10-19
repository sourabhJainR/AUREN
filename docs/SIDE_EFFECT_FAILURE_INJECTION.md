# Durable side-effect failure-injection gate

This gate exercises the local boundary between `ActionGovernanceGateway` and `DurableSideEffectLedger`. It uses provider-neutral fault wrappers and does not claim to prove a real provider's idempotency or production isolation.

## Required failure cases

- Intent persistence failure before dispatch: the handler does not run.
- Receipt persistence failure after a provider call: restart blocks replay and requires reconciliation.
- Receipt commit with lost acknowledgement: restart replays the immutable receipt without a second provider call.
- Failure to record reconciliation: the durable intent remains unresolved and a later begin fails closed.

## Rollout and rollback

Enable `require_durable_ledger_for_external_effects` only for adapters that inject a durable ledger and pass local fault tests. If rollout fails, disable the adapter route or deny the consequential action. Never delete or reset unresolved ledger rows to restore availability. Resolve ambiguous operations using trusted provider lookup or operator-retained evidence, then use the trusted retry-authorization path. Keep a last-known-good adapter/configuration and a database backup before staging rollout.

## Evidence boundary

Repository tests prove only modeled local behavior. Each production adapter still needs a staging failure-injection record bound to adapter version, provider operation identity, ledger state, observed remote outcome, and rollback result. Independent isolation and Arena evidence remain separate gates.
