# AUREN Evidence Archive deployment

This package deploys the minimal receiver at `scripts/evidence_archive_server.py`
as a non-root container. It retains privacy-allowlisted engineering events and
verifies signed external evaluator/custody/isolation/operator receipts before
acceptance. It does **not** contain evaluator secrets, oracle answers, sealed cases,
or private signing keys.

## Before deployment

1. Choose an approved Python base image pinned to an immutable platform-specific
   image digest and an exact, approved `cryptography` version. Record the supply
   chain approval; do not leave the placeholders in `.env.archive.example`.
2. Create a random archive bearer token of at least 32 characters in your secret
   manager. Never commit it or paste it into a task payload.
3. Copy `.env.archive.example` to `.env.archive`, then replace all placeholders.
4. Copy `trusted-public-keys.example.json` to `trusted-public-keys.json` and replace
   the example with the operator-approved mapping of versioned signer IDs to
   base64url-encoded 32-byte Ed25519 public keys. Do not put private keys here.
5. Copy `revoked-signers.example.json` to `revoked-signers.json`; keep it as a JSON
   array of revoked signer IDs (or `[]` when none are revoked). Update this file
   through your key-revocation process.
6. Restrict these files and the Docker volume to the dedicated service administrator.
   The local `.gitignore` excludes the populated secret and key files.

## Start the service

Run from this directory after replacing every placeholder:

```powershell
docker compose --env-file .env.archive -f compose.yaml config --quiet
docker compose --env-file .env.archive -f compose.yaml up --build -d
docker compose --env-file .env.archive -f compose.yaml ps
```

The listener is published only on host loopback at `127.0.0.1:8765`. The container
binds internally to `0.0.0.0`, runs as UID/GID 10001, has a read-only root filesystem,
no Linux capabilities, no-new-privileges, bounded memory/CPU/PIDs, and a small
no-exec temporary filesystem. Put a trusted TLS reverse proxy on the host in front
of it and expose only `https://your-approved-host/v1/auren/events` to AUREN clients.
Do not expose the built-in HTTP listener directly to the public network.

Health check (run on the host):

```powershell
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).read().decode())"
```

The health endpoint does not prove receipt verification or backup health. Send a
signed non-production test receipt through the TLS endpoint, verify it appears in
the archive, then test a bad signature, revoked signer, missing signature, stale
receipt, role mismatch, and duplicate event ID. All invalid cases must be rejected.

## Connect AUREN

Configure the AUREN worker/service environment (not individual tasks):

- `AUREN_EVIDENCE_SINK_URL=https://your-approved-host/v1/auren/events`
- `AUREN_EVIDENCE_SINK_TOKEN` from the same secret manager value as
  `AUREN_ARCHIVE_TOKEN`

GitHub Actions records a local privacy-minimized CI evidence artifact automatically. If the sink is not configured, the recorder now emits an explicit warning that retention is limited to local/CI artifacts. For protected release or promotion workflows, set `AUREN_REQUIRE_EXTERNAL_EVIDENCE_SINK=true` so the evidence-recording step fails when external retention is not configured. Provision the URL/token as repository or organization secrets only after the archive is deployed and authenticated; do not commit them or place them in task payloads.

AUREN's retryable outbox sends allowlisted lifecycle/iteration events. Verified
external receipts are sent as a `signed_receipt` envelope. Failed, skipped, and
unknown outcomes remain evidence and cannot become passing gates. AUREN does not
silently export raw prompts, source code, task payloads, test output, sealed cases,
or oracle answers.

## Production retention requirements

This is a single-node receiver, **not a managed WORM service**. The SQLite volume is
mutable by a privileged host administrator. Before treating it as production evidence:

- replicate and back up the archive to a separately administered immutable/WORM
  store with encryption, retention locks, restore drills, and audit logging;
- monitor disk capacity, health, receipt rejection, repeated authentication failures,
  export-outbox depth, and backup lag;
- define signer ownership, key rotation, revocation, and recovery procedures;
- enforce tenant isolation if more than one AUREN deployment writes to the service;
- retain raw evaluator/oracle/corpus material only in their independent custody
  systems; retain signed receipt envelopes and digests here;
- test restore, access revocation, service restart, and event idempotency before
  relying on the archive for promotion decisions.

A signature proves that a trusted key signed a statement. It does not prove the
evaluator was independent, the holdout was uncontaminated, the sandbox was isolated,
or rollback actually worked. Those claims require separate custody, isolation-audit,
and operator-run campaign/rollback evidence.
