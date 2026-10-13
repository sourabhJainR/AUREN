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

Register `TrustedRepositoryContextProvider` as the execution controller's
operator-owned promotion context handler. Configure its repository root, expected
`owner/repository` slug, decision-record directory, authorized-approver policy
file, and canonical evidence ledger outside task payloads. It resolves origin and
HEAD through Git, rejects mismatched remotes and dirty worktrees, reads the
decision record by the controller-provided task ID, and supplies evidence IDs only
from the ledger for the exact live repository/head.

Restrict these operator files and the evidence database with OS/service-account
ACLs. A local file or SQLite database is not a cryptographic signature; use a
trusted verifier and independently controlled signing keys for external claims.

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
- External evaluator/oracle/corpus/isolation evidence is only marked
  `signature_verified=true` through signed-envelope ingestion.

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
