# Deployment-wide admission coordinator

## Scope

`portable.postgres_admission_coordinator.PostgreSQLAdmissionCoordinator`
provides atomic lease claims against one shared PostgreSQL database. It is
intended for hosts that use separate AUREN state roots. It does not treat a
shared SQLite file or a per-host registry as a deployment coordinator.

The coordinator is integrated with response-delivery worker slots and durable
task submission/requeue paths. It also owns a permanent cross-root work-key
identity registry. The local SQLite limits remain active as a second safety
layer. Shared admission is opt-in; when configured, an unavailable or
mismatched shared authority fails closed.

Pending-task leases use the durable task ID as the operation identity and a
stable operator-assigned instance ID plus task ID as the owner. The owner ID
must remain unchanged across restarts of that runtime root. A pending lease is
bounded to 24 hours. Before dispatch, the controller must reacquire/confirm the
lease; if it has expired and capacity is unavailable, the task stays queued and
is not executed. Requeue paths reserve capacity before returning a task to
`pending`. A stale lease can consume capacity until release or expiry, which is
safe but may temporarily reduce throughput.

## Provisioning

Use a dedicated PostgreSQL database/schema and a service identity managed by
the operator. Install the project's PostgreSQL driver in the host environment.
Run one provisioning step with a migration identity that can create the four
`auren_admission_*` tables:

```python
from psycopg import connect
from portable.postgres_admission_coordinator import PostgreSQLAdmissionCoordinator

coordinator = PostgreSQLAdmissionCoordinator(
    lambda: connect("postgresql://<operator-configured-dsn>"),
    namespace="production-eu",
    policy_version="v1",
    provision=True,
)
```

Do not put a DSN or password in source control, task payloads, logs, or issue
comments. Runtime hosts should use a secret manager or workload identity and
construct the coordinator with `provision=False`. Every host in one
deployment must use the same database, namespace, policy version, resource
names, and capacity values. The first claim pins each resource's capacity in
the shared resource-policy table; a later host with a different value is
rejected rather than silently changing the limit.

## Runtime wiring

Construct the coordinator once from the host's secret-managed PostgreSQL
connection. Inject it into both the controller and delivery supervisor. The
instance ID must be stable for the lifetime of one runtime root and unique
across roots:

```python
controller = ExecutionController(
    root,
    deployment_admission_coordinator=coordinator,
    deployment_instance_id="runtime-root-01",
)
```

A restarted process for that same root must use the same instance ID. A second
root must use a different ID. The local pending-task limit remains active; all
participating roots must also agree on the shared PostgreSQL resource capacity.

## Delivery supervisor wiring

Inject the coordinator and a stable operator-assigned host identity:

```python
delivery = ResponseDeliverySupervisor(
    controller,
    journal_path,
    admission_coordinator=coordinator,
    deployment_host_id="worker-host-01",
)
```

The host identity must remain stable across process restarts and unique across
hosts. The task ID is the idempotent operation identity. Leases use PostgreSQL
time, a monotonically increasing fencing generation, owner/token-fenced renew
and release, and bounded expiry. A host must stop work when renewal fails or
the coordinator cannot be reached. Never create a local-only fallback when
shared admission is configured.

Before enabling this feature for an existing deployment, stop new submissions
and reconcile every existing nonterminal task with a work key into the shared
identity registry. Each identity must retain its existing task ID, intent
digest, and stable runtime owner. Do not allow a second host to accept new work
until reconciliation is complete. Existing tasks without a work key cannot be
retroactively deduplicated; assign keys only through an operator-reviewed
migration that preserves their original task identity.

## Rollout and rollback

1. Provision the shared database and policy row.
2. Verify all hosts can read the same policy version and can claim/renew/release
   test leases in one namespace.
3. Start with one canary host and a low capacity. Confirm a second host is
   denied at saturation.
4. Exercise process death, lease expiry, same-operation retry, stale-token
   renewal/release, database outage, and policy mismatch.
5. Enable the shared coordinator on all delivery hosts only after those checks.
6. To roll back, stop new delivery admission, drain or cancel active workers,
   disable coordinator injection on every host as one operator-controlled
   change, then retain the local SQLite cap. Do not drop PostgreSQL tables
   while any host may still use them.

## Evidence still required

The repository now includes service-backed PostgreSQL tests for permanent
work-key identity, same-owner recovery, cross-owner duplicate rejection, and
intent mismatch. These tests run only when `AUREN_TEST_POSTGRES_DSN` is
configured. They do not replace operator staging across at least two distinct
runtime roots and hosts.

Before closing issue #1193, complete the two-host staging rehearsal: process
death after identity claim and before local insert; restart with the same owner;
process death after local insert; duplicate submission from another owner;
intent mismatch; pending lease expiry/reacquisition; requeue; coordinator
outage; policy mismatch; and rollback. Capture host identities, config/policy
versions, task IDs, intent digests, timestamps, lease outcomes, and rollback
evidence. Keep local SQLite admission enabled. Do not close #1193 until
operator-controlled staging evidence is reviewed.
