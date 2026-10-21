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

To watch one or more exact GitHub commit heads, supply the full SHA and every required check name:

```bash
python auren_cli.py continuous start \
  --project-root . \
  --watch sourabhJainR/AUREN@FULL_40_CHARACTER_SHA \
  --required-check validate \
  --required-check "unit-tests (3.12, 3)"
```

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

## Current boundary

The continuous loop, exact-head check polling, stop command, task inventory and hash-chain audit are implemented. Remaining gates are: automatically translating a failed required check into a bounded, evidence-linked repair task; cross-store crash injection for the recovery ledger versus decision journal; child-process hard termination/deadline propagation; external side-effect reconciliation; independent evaluator/isolation custody; and a real staging rollback rehearsal. These must remain visible as pending rather than being marked complete by a green polling result.
