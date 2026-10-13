# Operator rollback rehearsal — required before consequential promotion

This runbook is an acceptance gate, not a claim that AUREN has already passed a
real operator rehearsal. A test against temporary directories proves only the
local helper contract. A deployment operator must execute this procedure against
a disposable staging environment that uses the same state store, service manager,
checkpoint format, identity provider, and release path as production.

## Preconditions

- Identify the exact repository and candidate head SHA.
- Identify the last-known-good commit and the rollback trigger.
- Confirm a tested backup and a maintenance window; stop writers before state-root
  copying or configuration switching.
- Identify the operator, target environment, service, and independent evidence
  signer. The signer must be controlled outside the evaluated runtime.
- Ensure the canonical evidence ledger and approved HTTPS retention sink are
  available. Do not put credentials, raw task payloads, sealed corpus cases, or
  oracle answers in the evidence receipt.

## Rehearsal steps

1. **Record the starting state.** Capture the candidate SHA, last-known-good SHA,
   active state-root identifier, task/checkpoint counts, and SHA-256 manifests.
2. **Exercise the real stop boundary.** Stop the service using the deployment's
   normal service manager and verify that no writer remains active.
3. **Preserve current state.** Take a restorable backup or snapshot of the current
   state root. Verify the backup before switching anything.
4. **Switch to last-known-good.** Restore the previous deployment/configuration
   pointer and state-root selection using the normal release/rollback mechanism.
   Keep the failed candidate state as a separate diagnostic artifact.
5. **Restart and verify.** Start the service and verify database integrity,
   checkpoint/task counts, durable queue recovery, handler registration, and
   representative task resume. Verify the active commit/config equals the
   declared last-known-good target.
6. **Exercise the failure path.** In staging only, make a required health or
   resume check fail and confirm the deployment does not mark rollback successful.
   Confirm alerts and the operator escalation path fire.
7. **Retain the result.** The independent operator system signs a closed-schema
   receipt binding repository, candidate SHA, target ID, operator ID, rollback-plan
   digest, last-known-good SHA, recovered SHA, outcome, run ID, issuance time, and
   an HTTPS URI for the retained receipt. Use the existing external evidence
   ingestion path to verify and retain that receipt.
8. **Restore staging.** Restore the staging environment to its agreed baseline and
   verify it is healthy. Keep the rehearsal artifacts according to retention policy.

## Evidence contract

A successful `source_type=operator`, `evidence_type=rollback_rehearsal` receipt must
include:

- `operator_id` and `target_id`;
- `rollback_plan_digest` (SHA-256);
- `last_known_good_commit` and `recovered_head_sha` (full commit SHAs);
- exact `repository` and `head_sha` of the candidate under test;
- signed receipt URI, payload digest, unique evidence ID/run ID, status, and
  timezone-aware issuance timestamp.

For a successful receipt, `recovered_head_sha` must equal
`last_known_good_commit`. Failed rehearsals must also be retained as negative
evidence but must not be eligible to satisfy promotion requirements. The trusted
signer must be distinct from the evaluated runtime, evaluator, and oracle.

## Acceptance checklist

- [ ] Stop/start and writer-quiescence verified.
- [ ] Backup and state-root manifests verified.
- [ ] Last-known-good configuration and state root restored.
- [ ] Database/checkpoint/task integrity and resume verified.
- [ ] Failure-path and alerting behavior verified in staging.
- [ ] Signed operator receipt verified against an operator-trusted public key.
- [ ] Receipt indexed for the exact candidate SHA and retained in the external sink.
- [ ] Independent reviewer confirms evidence and rollback action.

Attach the signed receipt ID and external URI to issue #411. Do not close #411 or
allow high/critical-risk promotion until the operator has actually executed this
rehearsal and the exact-head promotion gate resolves its evidence ID to a
`passed`/`verified` canonical ledger record. Repository CI is not a substitute for
the live rehearsal.
