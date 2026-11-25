# Intra-organization agent messaging

This communication ledger is a durable message boundary, not a task scheduler,
execution authority, provider router, or substitute for AUREN's ExecutionController.

## Contract

- Membership and identity come from trusted host configuration; payloads cannot create identities.
- Messages are organization-scoped and admitted by a host-injected Guard callback both at enqueue and dispatch.
- Durable SQLite records, message threads, correlation IDs, body digests, bounded message size/capacity and idempotency keys.
- Dispatch is notification/proposal-only. Consequential work must be submitted to the canonical controller and trusted plan dispatch, under existing approvals, budgets and Guard.
- A delivered receipt means the delivery callback returned, not that the recipient accepted the work or verified it.
- Failed delivery is marked retryable and may have an ambiguous outcome. Consumers must deduplicate by message_id/idempotency key before effects; retry reconciliation remains an operator/host concern.
- No cross-organization routing is allowed by this instance. Separate instances must be configured and a cross-organization gateway must explicitly authorize any such path.
- Callback implementation is responsible for securely binding trusted identity, scope, privacy class and approved context; message text is untrusted.
- SQLite durability is local process-level storage; this is not multi-host coordination, cryptographic attestation, or a hardened sandbox.

## Example

```python
from portable.organization_messaging import AgentIdentity, OrganizationMessaging

members = {
    "manager": AgentIdentity("manager", "engineering", "manager"),
    "developer": AgentIdentity("developer", "engineering", "developer"),
    "qa": AgentIdentity("qa", "engineering", "qa"),
}
# Host callbacks: Guard admission; bounded notification/proposal receiver.
bus = OrganizationMessaging("messages.sqlite", "engineering", members,
    authorize=host_guard_admission, deliver=agent_inbox)
msg = bus.enqueue("manager", "developer", "TASK_REQUEST",
    {"task": "implement PDF encryption"}, idempotency_key="task-17-dev",
    thread_id="task-17")
bus.dispatch(msg.message_id)
# Developer proposes completion/review request; execution authority stays canonical.
```

Message kinds include query/response, task request/accept/reject, handoff,
status, blocked, review request/result, evidence submission and cancellation.
