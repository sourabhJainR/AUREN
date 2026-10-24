# PostgreSQL attestation replay registry

Status: implementation slice only. This adapter is not production-ready until a host-trusted verifier accepts real deployment evidence, the PostgreSQL integration suite passes on the exact PR head, and an independent reviewer signs off.

## Consistency and claim contract

Use one strongly consistent PostgreSQL primary shared by every verifier host. The adapter uses a primary key on `attestation_id`, a unique operation-ID index, and a single insert transaction. Concurrent attempts for the same attestation and binding have one winner. Repeating the same operation ID with the same binding is idempotent; a different operation ID is a replay. Reusing an attestation ID with a different binding raises `ReplayClaimConflict`.

The binding includes the attestation digest, issuer key ID, audience, purpose, absolute expiry, policy version, and digest of the active freshness policy. The caller must keep `claim_operation_id` stable when retrying the same logical verification after an ambiguous response. `ReplayClaimOutcomeUnknown` exposes that operation ID. Call `lookup_claim` with the same attestation ID, binding, and operation ID to reconcile; a backend error or binding conflict remains blocked. Never create a new operation ID to retry an unknown claim.

The PostgreSQL adapter is a DB-API client and does not silently fall back to SQLite or process-local storage. The default `production_ready` value is false. Production freshness accepts the adapter only when a trusted deployment-attestation verifier returns literal boolean `True` for evidence that identifies the PostgreSQL backend, shared primary, TLS, at least two independent verifier hosts, failover policy, retention policy, schema version, and binding schema version. A unit-test callback is not deployment evidence.

## CI integration

Architecture Integrity starts a disposable PostgreSQL 16 service and runs the registry suite with `AUREN_TEST_POSTGRES_DSN`. The suite covers independent processes, concurrent claims, restart persistence, idempotent operation IDs, binding conflicts, and an injected lost commit acknowledgement. Other test jobs skip the live integration test when the DSN is absent. A green fake-store test alone is not proof of cross-host behavior.

## Migration

1. Back up the existing replay table and retain the backup checksum.
2. Deploy the additive schema migration through a controlled database change. The adapter adds binding columns and a unique operation-ID index; legacy rows without binding metadata are rejected on replay.
3. Verify that all verifier hosts use the same primary, the same schema version, and the same signed deployment policy before enabling production freshness.
4. Keep legacy consumed IDs. Do not clear or recreate the table to resolve a migration conflict. If old rows have no binding fields, fail closed and resolve them through the operator runbook rather than treating them as unused.
5. Record the database migration ID, primary identity, TLS configuration evidence, failover policy ID, retention policy ID, and exact deployed code SHA in the evidence envelope.

## Key rotation, expiry, and retention

The issuer key ID is part of every binding. Add the new key to the trusted set before issuance switches; keep the old key available for verification only through the documented overlap period; then revoke it and retain the revocation evidence. Do not delete replay claims when a signing key rotates. Retain claims for at least the maximum attestation validity plus the allowed clock-skew window and the audit retention period. Claims past expiry may be archived only under a reviewed retention policy; they must not be deleted early while a token can still be accepted.

## Monitoring and incident handling

Alert on database connection errors, commit-unknown outcomes, binding conflicts, duplicate claims, schema-version mismatch, failover events, TLS failures, and unexpectedly low claim volume. Preserve the operation ID, attestation ID, binding digest, database transaction result, and exact source SHA; never log credentials, raw signatures, or sensitive payloads. On an ambiguous commit, stop acceptance for that request and reconcile with the same operation ID against the same strongly consistent primary. Do not automatically retry with a new ID.

## Rollback

Rollback must not restore SQLite as a production multi-host registry and must not delete consumed claims. If the new schema or adapter fails, disable production attestation acceptance at the verifier gate, keep the database table and audit evidence intact, and return an explicit blocked state. Restore the previous application only if it remains configured to fail closed for cross-host production use. Re-enable acceptance only after the schema, binding checks, deployment attestation, exact-head tests, and independent review pass again.

## Evidence still required before production use

- Real shared-PostgreSQL integration results from the exact PR head, including independent-process race and restart tests.
- Signed deployment evidence proving the shared-primary, TLS, independent-host, failover, retention, and schema claims.
- Independent code review and production migration/rollback rehearsal.
- Verified owner/admin ruleset and retained post-merge main workflow, bundle, and checksum artifacts.

Until those artifacts exist, production cross-host replay protection and external Arena readiness remain blocked.
