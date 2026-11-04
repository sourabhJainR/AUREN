# Continuous execution, implicit polling and interruptible audit

AUREN now has a durable continuous supervisor over the existing `ExecutionController` and task/progress stores. It does not create a second queue or elevate CI output into authorization.

## CLI

Run from the project checkout:

```bash
python auren_cli.py continuous start --project-root .
python auren_cli.py continuous status --project-root .
python auren_cli.py continuous audit --project-root .
python auren_cli.py continuous stop --project-root . --reason "operator requested pause"
```

`continuous start` remains active, automatically scans and executes eligible durable tasks, and polls configured CI watches on every cycle. It records cycle boundaries, poll outcomes, recoverable poll errors, task counts, stop requests, and terminal supervisor state. The stop command is durable and can be run from a second process; it does not delete pending tasks, checkpoints, progress history, or evidence. `status` shows pending task IDs and recent terminal tasks without exposing task payloads. `audit` verifies the append-only SHA-256 hash chain.

When no `--watch` is supplied, AUREN implicitly discovers the current checkout's GitHub `origin` and full `HEAD` SHA and polls that commit. Without explicit `--required-check` names, the result is labelled `observed_complete_unverified`; it is not CI-gate evidence and cannot authorize a merge. Configure exact required-check names once in the service invocation (or pass them on the command) when a verified CI gate is needed.

The default polling interval is 60 seconds to avoid exhausting unauthenticated GitHub API limits. Set `GH_TOKEN` or `GITHUB_TOKEN` for authenticated polling; `--poll-seconds` can then be tuned to the repository's rate budget. Stop requests are checked independently at short intervals, so a longer CI poll interval does not delay interruption by the full interval.

To watch one or more exact GitHub commit heads, supply the full SHA and every required check name:

```bash
python auren_cli.py continuous start \
  --project-root . \
  --watch sourabhJainR/AUREN@FULL_40_CHARACTER_SHA \
  --required-check validate \
  --required-check "unit-tests (3.12, 3)"
```

For automatic bounded repair, also pass `--repair-handler` with an already registered, reviewed handler name. Repair is enabled only when explicit required checks are configured. Failed checks are deduplicated by repository, immutable head SHA, exact check name, and failure fingerprint; repair tasks use the existing ExecutionController queue and EngineeringRecoveryLedger. Without a registered repair handler, failures remain visible but are not silently converted into executable tasks.

Repeat `--watch` for each commit and `--required-check` for each required check. Check names match exactly. A missing check remains pending; `skipped`, `neutral`, `cancelled`, timed-out and other non-success conclusions are not passes. API/network errors are recorded as poll errors and never interpreted as success. The poller is read-only: it does not merge, approve, or alter branch protection. A separate trusted execution task must consume verified exact-head evidence before promotion.

```bash
# Bounded diagnostic run; production continuous mode omits --max-cycles.
python auren_cli.py continuous start --project-root . --max-cycles 3
```

## Lifecycle and recovery contract

- One supervisor owns a finite renewable lease. A second live worker cannot drive the same project concurrently.
- A process crash leaves the run marked running with an expiring lease; a service manager can restart the command and recover after the lease expires. A stop request is intentionally durable and must be explicitly started again after the worker has acknowledged it.
- Every audit event is serialized and SHA-256 chained. Externally supplied poll data is bounded and sensitive-key fields are redacted before persistence.
- A poll error, missing required check, stale/missing task receipt, failed task, or skipped required check is never reported as success.
- The supervisor is not a substitute for installing it as a host service. For unattended operation, run `continuous start` under the OS service manager with restart-on-process-exit enabled; use `continuous stop` for a graceful, auditable interruption.
- CI polling observes an explicitly supplied immutable commit SHA. It does not discover arbitrary PRs or infer a merge target. The existing controller remains the authority for CI-gated and consequential operations.
- A configured repair handler now receives a durable `EngineeringRecoveryLedger`; repair attempts remain bounded by the same-hypothesis, total-attempt, and elapsed-time limits.
- Repository CI workflows cancel superseded runs for the same PR/ref and apply job-level timeouts. A newer commit therefore replaces stale validation work instead of waiting behind an unbounded queue. Only checks for the current immutable head count toward promotion.

## Restart on a host

A sample systemd unit is available at `deploy/systemd/auren-continuous.service.example`. Copy it to `/etc/systemd/system/auren-continuous.service`, edit the service user and checkout paths, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now auren-continuous.service
sudo systemctl status auren-continuous.service
```

Put `GH_TOKEN` or `GITHUB_TOKEN` in the root-readable `/etc/auren/continuous.env` file; do not place credentials in task payloads or commit them. For an approval-gated repair lane, edit `ExecStart` to include the exact `--watch OWNER/REPO@FULL_SHA`, each required `--required-check`, and an already registered `--repair-handler`. The handler must be reviewed and must preserve the existing evidence, authority, and promotion gates. The sample unit restarts the process after failures, while SQLite task/approval state and audit events remain on disk. A deliberate durable stop is not auto-undone; start the service again only after the stop has been acknowledged.

If a run appears stuck, inspect `continuous status` and `continuous audit` first. Do not start a second worker against the same project root. Check the exact-head workflow runs and the service journal; stale runs are cancelled by concurrency policy, failed checks remain failures, and job timeouts bound the wait. After fixing the cause, let the current head's checks complete rather than repeatedly pushing cosmetic commits.

## Current boundary

The continuous loop, exact-head check polling, stop command, task inventory and hash-chain audit are implemented. Remaining gates are: connecting each deployed repair handler to its provider-specific evidence and review contracts; cross-store crash injection for the recovery ledger versus decision journal; child-process hard termination/deadline propagation; external side-effect reconciliation; independent evaluator/isolation custody; and a real staging rollback rehearsal. These must remain visible as pending rather than being marked complete by a green polling result.
