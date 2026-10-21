# Provider effect reconciliation adapter

AUREN uses the existing DurableSideEffectLedger. This adapter does not create a second ledger or scheduler.

## Host contract

The host supplies two callbacks.

- provider_lookup receives the durable ledger key and payload digest. It queries the provider's operation history by a stable operation identity.
- evidence_verifier checks the returned evidence against the requested operation. It must use a trusted source. A receipt from an agent or task payload is not trusted evidence.

A provider lookup result contains an evidence reference and a controller-compatible receipt. The receipt contains the original operation result under the result field.

## Recovery behavior

- A verified provider operation becomes an immutable completed receipt.
- A later matching dispatch replays the receipt. It does not invoke the handler again.
- A provider outage, malformed response, or rejected evidence leaves the operation blocked.
- A lookup that finds no operation does not prove that no effect occurred. It does not authorize a retry.
- A retry still requires the existing trusted operator authorization path.

## Rollback

Stop new consequential dispatches before changing adapter wiring. Preserve the execution database and evidence references. Inventory every ambiguous operation before disabling the adapter. Do not delete ledger records or change operation identity to make a retry possible.

## Evidence boundary

This adapter is an integration point. It does not prove that a provider supports idempotency or operation-history lookup. It does not prove production isolation or independent evaluation. Those gates require separate evidence.
