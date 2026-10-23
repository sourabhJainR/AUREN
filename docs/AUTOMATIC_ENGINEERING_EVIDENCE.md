# Automatic engineering evidence capture and external retention

## Purpose

AUREN records the engineering trail automatically as its supervised runtime works:
task submission, each execution attempt, gate rejection, task completion/failure,
and signed evidence ingested from external systems. No per-task prompt or manual
declaration is required. The evidence trail is separate from the authority system:
recording an event never authorizes promotion.

## What is retained

- Work-unit/task IDs, lifecycle phase, attempt number, status, timestamps.
- Safe structured metadata such as handler name, failure class, regression counts,
  retry/recovery category, external run ID, and repository/candidate commit SHA.
- SHA-256 digests and immutable references for artifacts and external evidence.
- A local append-only SQLite journal with a hash chain.
- Verified external signed receipt envelopes in a dedicated table, plus a canonical
  ledger entry bound to repository, exact head SHA, signer, status, URI and digest.
- A retryable export outbox for a configured external HTTPS retention service.

The event API uses an allowlist. It rejects arbitrary fields such as prompts,
payloads, source text, stack traces, API keys, passwords, raw test output, oracle
answers, or sealed holdout cases. The external receipt schema also rejects unknown
fields. Store signed receipts as metadata/digests/references; keep corpus cases,
private oracle labels and evaluator signing keys in their independent custody
systems. Never place private signing keys in AUREN.

## Automatic local capture

`ExecutionController` creates `.auren/engineering-evidence.sqlite3` and records
submission, attempt, completion and failure events without copying the task payload.
Iteration number is the durable controller attempt number. Existing task execution
semantics remain unchanged when no remote sink is configured.

## Configure remote retention once per deployment

Configure the service environment, not individual tasks:

- `AUREN_EVIDENCE_SINK_URL` — HTTPS endpoint controlled by the deployment operator.
- `AUREN_EVIDENCE_SINK_TOKEN` — short-lived/rotatable bearer credential injected by
  the deployment secret manager.

Example (replace the URL with your approved retention service; do not commit a token):

```powershell
$env:AUREN_EVIDENCE_SINK_URL = "https://evidence.example.org/v1/auren/events"
$env:AUREN_EVIDENCE_SINK_TOKEN = "<inject-from-your-secret-manager>"
```

The sink receives structured event metadata. For a verified external receipt it
also receives the signed receipt envelope and receipt digest. The remote service
should enforce tenant isolation, authenticated writer identity, append-only or
WORM retention, retention/deletion policy, audit logging, and encrypted backups.
AUREN's generic HTTPS adapter cannot guarantee those server-side properties; they
must be validated for the actual external service.

## Run the included receiver

AUREN includes `portable.evidence_archive.EvidenceArchive` and
`scripts/evidence_archive_server.py` as a minimal receiver. It authenticates the
bearer token, verifies the event digest, rejects conflicting reuse of an event ID,
stores signed receipt envelopes, and independently verifies receipt signatures
using a trusted public-key file.

A hardened container deployment package is available at
[`deploy/evidence-archive/`](../deploy/evidence-archive/README.md). It pins the
Python base image by operator-supplied digest, pins the cryptography package version,
runs non-root with a read-only filesystem and resource limits, binds the host port
to loopback, and documents TLS proxying, key management, immutable backup, and
restore/revocation tests. The package is a deployable receiver, not proof that an
external archive has been provisioned or that WORM retention is enabled.

1. Create a JSON file outside the repository mapping trusted signer IDs to base64url
   Ed25519 public keys, and optionally a JSON list of revoked signer IDs.
2. Inject `AUREN_ARCHIVE_TOKEN` (at least 32 characters) from your secret manager,
   `AUREN_ARCHIVE_TRUSTED_PUBLIC_KEYS_FILE`, and optionally
   `AUREN_ARCHIVE_REVOKED_SIGNERS_FILE`.
3. Run `python -m scripts.evidence_archive_server` on a dedicated service account.
   The default listener is `127.0.0.1:8765`; keep it on loopback and configure a
   trusted TLS reverse proxy for `https://your-approved-host/v1/auren/events`.
4. Configure AUREN clients with that HTTPS URL and the matching sink token.
5. Restrict archive DB access and back it up to an independently controlled
   immutable/WORM store. Test restore, retention, access revocation, and alerting.

The included receiver is a small single-node archive, not a managed WORM service:
it provides authenticated ingestion and immutable event-ID semantics, but OS-level
administrators can still rewrite its SQLite file. Production deployments must use
independent backups/WORM retention, access logging, key rotation/revocation, and
tenant isolation. Never expose the built-in plain-HTTP listener directly to the
public network.

The Architecture Integrity workflow now records its exact-head outcome automatically
and retains a 90-day GitHub Actions artifact. The local outbox retries delivery when
new events are recorded. The durable supervisor also retries delivery during health
checks. If a promotion gate is configured and remote export is enabled, pending
evidence exports block promotion.
Do not configure a remote sink unless it is approved to receive the allowlisted
metadata and signed attestation envelopes.

## Ingest signed external evidence

The external CI, evaluator, oracle-custody, sealed-corpus-custody, isolation-audit,
or operator-rollback system should retain the signed receipt itself and supply an
envelope with exactly `payload`, `signer_id`, and `signature`. The signed payload
binds at least:

- unique evidence ID and external run ID;
- repository slug and full 40-character candidate commit SHA;
- evidence type, outcome/status, SHA-256 payload digest, and HTTPS receipt URI;
- timezone-aware issuance time;
- approved source type;
- optional evaluator/oracle/runtime identities, corpus digest, isolation profile,
  and external system identity.

Use a verifier backed by operator-trusted public keys (for example, the existing
Ed25519 detached-signature verifier). The key is never accepted from the envelope.
A receipt is rejected if its signature fails, its signer is the evaluated runtime,
evaluator or oracle, its repository/head is stale, its timestamp is outside the
freshness window, its schema contains unknown fields, or the canonical evidence ID
conflicts with previously retained content. The receipt envelope is retained
locally only after signature and schema verification, and is sent to the remote
retention sink if one is configured.

The source URI must point to the external system's retained signed receipt. The
AUREN journal stores receipt metadata/digests and the signed envelope—not the
sealed corpus or secret oracle answers.

## Trusted live repository context

Configure `AUREN_REPOSITORY_ROOT`, `AUREN_REPOSITORY_SLUG`,
`AUREN_DECISION_RECORD_DIR`, `AUREN_AUTHORIZED_APPROVERS_FILE`, and optionally
`AUREN_CANONICAL_EVIDENCE_DB` in the service environment. Register the durable
module-level adapter as the execution controller's context handler:

```python
controller.register_handler(
    "trusted-context-provider",
    "portable.trusted_repository_context:trusted_repository_context",
    execution_class="context_provider",
)
# Then use promotion_gate_policy={"context_handler": "trusted-context-provider"}
# when registering each consequential handler.
```

The adapter constructs `TrustedRepositoryContextProvider` from operator-owned
configuration. It resolves origin and HEAD through Git, rejects mismatched remotes
and dirty worktrees, reads the
decision record by the controller-provided task ID, and supplies evidence IDs only
from the ledger for the exact live repository/head.

Every evidence ID used by a passed check, approval, or rollback requirement must
resolve to a retained signed receipt. The provider rechecks signer trust, signature,
receipt digest, freshness, repository/head, status, and evidence role at promotion
time. Ordinary evidence defaults to a seven-day freshness window; rollback rehearsal
receipts may be retained for up to 90 days, subject to key revocation and the
operator's risk policy. Restrict operator files and databases with OS/service-account
ACLs. The SQLite ledger is not a cryptographic signature; external claims rely on
trusted public keys and independently controlled signing identities.

## Handler effect classification rollout

Every durable handler must now declare an operator-owned `execution_class` at
registration:

- `read_only`: no consequential external side effects; caller is responsible for
  code review and least-privilege credentials.
- `context_provider`: internal-only trusted context/evidence lookup. These handlers
  may be invoked by preflight gates but cannot be submitted as user tasks.
- `ci_gated`: a bounded action that requires exact-head CI evidence.
- `consequential`: promotion/merge/release/deploy/publish or other consequential
  engineering action; both decision-record authorization and exact-head CI gates
  are mandatory.

The classification is persisted outside task payloads and cannot be downgraded by
re-registering an existing handler. Action-like handler names or callable identities
cannot be classified read-only. Old database rows are migrated to
`unclassified` and fail closed until an operator explicitly re-registers each
handler with its reviewed class and required gate policies. Do not bulk-mark unknown
handlers read-only to bypass the migration. Inventory every registered handler,
review its side effects, and classify it deliberately before enabling production
workers. This is a compatibility/security migration: unclassified legacy tasks
will not execute.

## Regression and iteration evidence

- Every durable controller attempt is recorded automatically.
- Architecture Integrity CI emits a safe exact-head workflow outcome and stores it as a CI artifact; configure the external sink secrets to also deliver it to centralized retention.
- Gate rejections and failures remain in the trail; successful retries do not erase
  earlier failures.
- CI adapters should call `record("regression_passed", ...)` or `record("regression_failed", ...)`
  with suite names, counts, run IDs, exact head SHA, and artifact digests—not raw output.
- Recovery adapters should record the selected retry/alternate-plan/rollback
  category and a digest of the hypothesis or plan, not private chain-of-thought or
  raw prompt content.
- External evaluator/oracle/corpus/isolation evidence is only treated as verified
  when its signed envelope has been checked by the trusted verifier and retained in
  the dedicated signed-receipt store; caller-supplied event metadata cannot assert it.

## Limits and acceptance gates

The SQLite hash chain detects accidental or unsophisticated edits but does not
prevent a privileged host administrator from rewriting the database. Production
retention needs a separate identity, signing key, and immutable/WORM destination.
A signed receipt proves what the trusted signer attested; it does not alone prove
the evaluator's independence or the sandbox's isolation. Those require separate
custody, access-control, and operator audit evidence.

This infrastructure does not replace branch protection, independent evaluation,
complete promotion-bypass testing, or a real operator rollback rehearsal. Those
remain mandatory P0 acceptance gates.

## Outbox claim and crash recovery

The SQLite outbox claims each batch inside a write-locked transaction. A claimed
row enters `sending` with a unique claim token and a 120-second lease. Concurrent
local flushes skip an unexpired claim. A later flush returns expired claims to
`pending` and retries them with the same event ID. The schema migration is
additive and preserves existing outbox rows.

`pending_exports()` counts every state other than `sent`, including in-flight and
unfamiliar states, so a promotion gate remains blocked while delivery is unresolved.
A stale worker may not finalize a row after a newer claim takes ownership.

Delivery is at-least-once, not exactly-once. A process can die after the remote
service accepts an event but before local finalization, and a slow request may
outlive its lease. The sink must deduplicate on the stable event ID; the HTTPS
adapter sends it as `Idempotency-Key`. Unknown remote outcomes must not be treated
as proof of delivery. If reverting this code, preserve the evidence journal,
canonical ledger, signed receipts, and outbox rows; the added columns are safe to
leave in place.

## Ingest signed external evidence

The external CI, evaluator, oracle-custody, sealed-corpus-custody, isolation-audit,
or operator-rollback system should retain the signed receipt itself and supply an
envelope with exactly `payload`, `signer_id`, and `signature`. The signed payload
binds at least:

- unique evidence ID and external run ID;
- repository slug and full 40-character candidate commit SHA;
- evidence type, outcome/status, SHA-256 payload digest, and HTTPS receipt URI;
- timezone-aware issuance time;
- approved source type;
- optional evaluator/oracle/runtime identities, corpus digest, isolation profile,
  and external system identity.

Use a verifier backed by operator-trusted public keys (for example, the existing
Ed25519 detached-signature verifier). The key is never accepted from the envelope.
A receipt is rejected if its signature fails, its signer is the evaluated runtime,
evaluator or oracle, its repository/head is stale, its timestamp is outside the
freshness window, its schema contains unknown fields, or the canonical evidence ID
conflicts with previously retained content. The receipt envelope is retained
locally only after signature and schema verification, and is sent to the remote
retention sink if one is configured.

The source URI must point to the external system's retained signed receipt. The
AUREN journal stores receipt metadata/digests and the signed envelope—not the
sealed corpus or secret oracle answers.

## Trusted live repository context

Configure `AUREN_REPOSITORY_ROOT`, `AUREN_REPOSITORY_SLUG`,
`AUREN_DECISION_RECORD_DIR`, `AUREN_AUTHORIZED_APPROVERS_FILE`, and optionally
`AUREN_CANONICAL_EVIDENCE_DB` in the service environment. Register the durable
module-level adapter as the execution controller's context handler:

```python
controller.register_handler(
    "trusted-context-provider",
    "portable.trusted_repository_context:trusted_repository_context",
    execution_class="context_provider",
)
# Then use promotion_gate_policy={"context_handler": "trusted-context-provider"}
# when registering each consequential handler.
```

The adapter constructs `TrustedRepositoryContextProvider` from operator-owned
configuration. It resolves origin and HEAD through Git, rejects mismatched remotes
and dirty worktrees, reads the
decision record by the controller-provided task ID, and supplies evidence IDs only
from the ledger for the exact live repository/head.

Every evidence ID used by a passed check, approval, or rollback requirement must
resolve to a retained signed receipt. The provider rechecks signer trust, signature,
receipt digest, freshness, repository/head, status, and evidence role at promotion
time. Ordinary evidence defaults to a seven-day freshness window; rollback rehearsal
receipts may be retained for up to 90 days, subject to key revocation and the
operator's risk policy. Restrict operator files and databases with OS/service-account
ACLs. The SQLite ledger is not a cryptographic signature; external claims rely on
trusted public keys and independently controlled signing identities.

## Handler effect classification rollout

Every durable handler must now declare an operator-owned `execution_class` at
registration:

- `read_only`: no consequential external side effects; caller is responsible for
  code review and least-privilege credentials.
- `context_provider`: internal-only trusted context/evidence lookup. These handlers
  may be invoked by preflight gates but cannot be submitted as user tasks.
- `ci_gated`: a bounded action that requires exact-head CI evidence.
- `consequential`: promotion/merge/release/deploy/publish or other consequential
  engineering action; both decision-record authorization and exact-head CI gates
  are mandatory.

The classification is persisted outside task payloads and cannot be downgraded by
re-registering an existing handler. Action-like handler names or callable identities
cannot be classified read-only. Old database rows are migrated to
`unclassified` and fail closed until an operator explicitly re-registers each
handler with its reviewed class and required gate policies. Do not bulk-mark unknown
handlers read-only to bypass the migration. Inventory every registered handler,
review its side effects, and classify it deliberately before enabling production
workers. This is a compatibility/security migration: unclassified legacy tasks
will not execute.

## Regression and iteration evidence

- Every durable controller attempt is recorded automatically.
- Architecture Integrity CI emits a safe exact-head workflow outcome and stores it as a CI artifact; configure the external sink secrets to also deliver it to centralized retention.
- Gate rejections and failures remain in the trail; successful retries do not erase
  earlier failures.
- CI adapters should call `record("regression_passed", ...)` or `record("regression_failed", ...)`
  with suite names, counts, run IDs, exact head SHA, and artifact digests—not raw output.
- Recovery adapters should record the selected retry/alternate-plan/rollback
  category and a digest of the hypothesis or plan, not private chain-of-thought or
  raw prompt content.
- External evaluator/oracle/corpus/isolation evidence is only treated as verified
  when its signed envelope has been checked by the trusted verifier and retained in
  the dedicated signed-receipt store; caller-supplied event metadata cannot assert it.

## Limits and acceptance gates

The SQLite hash chain detects accidental or unsophisticated edits but does not
prevent a privileged host administrator from rewriting the database. Production
retention needs a separate identity, signing key, and immutable/WORM destination.
A signed receipt proves what the trusted signer attested; it does not alone prove
the evaluator's independence or the sandbox's isolation. Those require separate
custody, access-control, and operator audit evidence.

This infrastructure does not replace branch protection, independent evaluation,
complete promotion-bypass testing, or a real operator rollback rehearsal. Those
remain mandatory P0 acceptance gates.
