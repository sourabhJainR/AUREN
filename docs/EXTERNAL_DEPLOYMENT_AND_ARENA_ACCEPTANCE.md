# External deployment and Arena acceptance runbook

This runbook separates repository implementation from evidence that only an
independent operator can produce. It does not grant AUREN access to deployment
secrets, production hosts, the PostgreSQL service, sealed corpus material, or
external signing keys.

## Evidence rules

- Use the actual deployment PostgreSQL service for #1193 and #909. A disposable
  CI service, Docker network, test callback, or simulated host is not acceptable.
- Use at least two separately administered physical hosts with distinct
  hardware/cloud instance identities, workload principals, runtime roots, and
  host-attestation records. Two containers on one runner are not two hosts.
- Bind every observation to the exact deployed AUREN commit, PostgreSQL service
  and cluster identity, primary identity/epoch, schema migration, policy version,
  configuration digest, and UTC timestamp.
- Keep credentials, DSNs, private keys, sealed cases, and oracle answers out of
  manifests, logs, GitHub issues, and artifacts. Store only opaque references
  and non-secret digests.
- Preserve raw evidence in a separately administered immutable archive. A
  manifest passing the local validator is only a completeness result; it does
  not authenticate references or prove independence.
- Unknown, missing, contradictory, or failed observations remain blocked.
  Never replace an unavailable check with a green unit test.

## #1193 — physical multi-host admission staging

### Preconditions

1. The deployment operator identifies two physical hosts and two stable AUREN
   runtime roots. Record independently authenticated host identities and
   workload principals.
2. Both hosts use the same operator-managed PostgreSQL service, namespace,
   admission policy version, pending capacity, delivery-worker capacity, and
   schema migration. Confirm TLS and server identity from each host.
3. Provisioning is performed once by the database migration identity. Runtime
   hosts use least-privilege workload identities and cannot alter policy rows
   or schema.
4. Keep local SQLite limits enabled. Do not enable a local-only fallback when
   shared admission is configured.
5. Stop new submissions while reconciling existing work-key identities on
   every runtime root. Preserve task IDs, intent digests, and owner bindings.

### Rehearsal sequence

Run with the real deployment service in an approved staging window:

1. Start host A and host B with separate roots and distinct stable
   `deployment_instance_id` / `deployment_host_id` values.
2. Submit the same canonical work key concurrently from both hosts. Require
   exactly one accepted task and one cross-owner duplicate rejection.
3. Submit the same key with a changed intent digest. Require rejection.
4. Saturate the shared pending-task and delivery-worker limits from both hosts.
   Require the aggregate count, not each host's local count, to remain within
   the configured deployment limit.
5. Kill the process after permanent identity commit but before local insertion.
   Restart the same root with the same owner and require recovery of the
   canonical task ID. A different owner must remain rejected.
6. Kill a process after local insertion and restart it. Require idempotent
   recovery without duplicate dispatch.
7. Expire a pending lease and force reacquisition. Require a new fencing
   generation and reject stale-token renew/release attempts.
8. Interrupt PostgreSQL connectivity from one host. Require new admission and
   dispatch to fail closed; verify there is no SQLite-only bypass.
9. Introduce a policy/capacity mismatch on the second host. Require the host to
   be denied before task insertion or dispatch.
10. Exercise the operator rollback procedure: stop new admission, drain or
    cancel active workers, disable shared admission on all hosts as one
    controlled change, retain local SQLite caps, and preserve PostgreSQL
    identity/lease data. Verify the prior state and task inventory.
11. Restore the shared coordinator only after policy, identity, and lease state
    are reconciled. Capture the operator's go/no-go decision.

### Required manifest

Create an operator-owned JSON manifest using schema
`auren-postgres-admission-staging-v1`. It must contain:

- exact main SHA, deployment ID, PostgreSQL service/cluster/primary identity,
  PostgreSQL version, deployed schema and migration ID;
- policy version, shared capacity values, configuration digest, and TLS result;
- two or more distinct physical host IDs, host IDs, runtime-root IDs,
  workload credential principals, timestamps, per-host config digests, and
  host-identity attestation references;
- distinct operator, database administrator, security reviewer, and
  independent reviewer identities;
- external artifact references for the change approval, configuration,
  policy attestation, race, restart recovery, lease fencing, outage,
  rollback, and independent review;
- explicit true results for duplicate and intent-mismatch rejection,
  same-owner restart recovery, fencing, fail-closed outage, and verified
  operator-controlled rollback.

Validate the manifest:

```bash
python scripts/postgres_deployment_evidence.py /secure/path/admission-staging.json
```

A successful result means only that the manifest is structurally complete.
The independent reviewer must authenticate every referenced artifact and
confirm the hosts are separate physical failure domains.

## #909 — production PostgreSQL consistency and failover evidence

Run this separately from #1193. Admission success does not prove attestation
replay consistency.

1. Enumerate every production verifier host and independently attest its
   physical host identity, workload principal, deployed AUREN SHA, config
   digest, trust-policy version, database service identity, and TLS server
   identity.
2. Prove every verifier connects to the same strongly consistent PostgreSQL
   primary service. Record the consistency mode, cluster identity, primary
   identity, schema/migration version, and observed primary epoch.
3. From separate verifier hosts, race one attestation ID and binding with
   distinct operation IDs. Require one claim winner. Replay the consumed claim
   from every host and after restart; require rejection.
4. Repeat with a mismatched digest, issuer key ID, audience, purpose, expiry,
   policy version, and policy digest. Require rejection for every changed
   binding.
5. Inject a lost commit acknowledgement. Reconcile only with the original
   operation ID against the same authoritative primary. Do not mint a new
   operation ID for an ambiguous claim.
6. Perform an operator-approved database failover. Record the old and new
   primary identities/epochs, timeline or equivalent provider failover
   evidence, consistency checks, and service endpoint resolution from each
   verifier host.
7. After failover, verify the consumed attestation remains consumed from every
   verifier host. Confirm no split-brain or stale-replica acceptance path.
8. Interrupt database access and force a timeout/partition. Require all
   verifiers to fail closed. Restore service and reconcile unknown operations
   without duplicate claims.
9. Verify retention and backup policy, schema compatibility, TLS configuration,
   key rotation and revocation, monitoring alerts, and operator recovery.
10. Have an independent reviewer compare the evidence to the exact deployed
    configuration and approve or reject production freshness.

Use schema `auren-postgres-attestation-production-v1` and validate:

```bash
python scripts/postgres_deployment_evidence.py /secure/path/attestation-production.json
```

The manifest must include at least two distinct physical hosts, the same
PostgreSQL service, the consistency mode, failover policy, retention policy,
schema attestation, TLS attestation, consistency probe, cross-host replay race,
restart recovery, failover evidence, and before/after primary epochs. A changed
epoch is necessary but not sufficient: the independent reviewer must verify
the actual provider/database failover records and post-failover replay results.

Until signed external evidence is accepted, keep production freshness
fail-closed. Do not close #909 from CI or a staging-only database.

## #404 — independent Arena campaign

The repository already contains the gateway, sealed request/receipt binding,
signature verification, replay controls, external evidence archive, and
production-gate manifest validation. These are execution and verification
primitives, not proof that an independent campaign has happened.

The campaign owner must be outside the AUREN development and runtime
administration boundary:

1. An independent operator selects an unseen task family and project.
2. A separate custodian creates and seals the holdout corpus. The evaluator
   runtime and AUREN operators must not receive the cases or oracle answers.
3. An independent evaluator runs the pinned AUREN build in an audited isolated
   environment. Record exact source SHA, runtime/evaluator image digests,
   configuration digest, corpus manifest digest, nonce, and isolation receipt.
4. A separately administered external signing authority signs the campaign
   request/result receipt. Its private key and root-update authority stay
   outside AUREN. Record the signer identity, trust-root version, validity,
   revocation status, and signature-verification receipt.
5. Run at least twice with fresh nonces. Include replay, corpus mismatch,
   invalid/expired signature, role overlap, missing isolation, and partial
   receipt coverage controls. Every negative control must be rejected.
6. Retain raw receipts, sanitized results, evaluator logs, and the independent
   review in an immutable archive administered by an organization distinct
   from the AUREN operator. Record archive object IDs, retention lock, timestamps,
   and content digests. Do not put holdout material or oracle answers in the
   archive visible to AUREN.
7. Publish only measured results, baseline comparison, uncertainty, failures,
   resource cost, and limitations. Do not present one campaign as AGI proof.

Validate the existing campaign/migration evidence manifest contract with:

```bash
python scripts/production_gate_evidence.py /secure/path/campaign-evidence.json
```

The independent reviewer must separately verify that the evaluator, oracle,
custodian, signer, operator, and result archivist are distinct principals and
that the external archive is not administered by the AUREN team. A valid
signature authenticates a statement under a key; it does not prove signer
independence or statement truth.

## Acceptance and rollback

Do not close #1193, #909, or #404 until the corresponding external evidence
has been independently authenticated and retained. A validator pass, GitHub
Actions artifact, internal signed fixture, Docker simulation, or self-review is
not a substitute.

If any production rehearsal fails or evidence becomes uncertain:
- stop admission, production attestation acceptance, or campaign promotion as
  applicable;
- preserve task identities, replay claims, source state, raw logs, and evidence;
- revert only through the operator-approved rollback procedure;
- keep the relevant trust gate fail-closed;
- record the failure and repeat only after the cause and recovery are reviewed.
