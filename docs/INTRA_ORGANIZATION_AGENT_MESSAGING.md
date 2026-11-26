# Intra-organization agent messaging

This communication ledger is a durable message boundary, not a task scheduler,
execution authority, provider router, or substitute for AUREN's ExecutionController.

## Contract

- Membership and identity come from trusted host configuration; payloads cannot create identities.
- Messages are organization-scoped and admitted by a host-injected Guard callback both at enqueue and dispatch.
- Durable SQLite records, message threads, correlation IDs, body digests, bounded message size/capacity and idempotency keys.
- Dispatch is notification/proposal-only. Consequential work must be submitted to the canonical controller and trusted plan dispatch, under existing approvals, budgets and Guard.
- A delivered receipt means the delivery callback returned, not that the recipient accepted the work or verified it.
- Delivery is claimed atomically before callback invocation. The receiver gets both message_id and idempotency_key and must deduplicate effects by those stable identities.
- A delivery callback exception is an uncertain outcome, never an automatic retry. A process crash after claim also leaves an in-flight message that requires reconciliation.
- pending() returns only queued/retryable messages. uncertain() exposes in-flight/uncertain messages separately; dispatch refuses them until reconciled.
- A trusted host may inject reconcile_delivery(recipient, envelope). It must perform an authoritative receiver-side lookup and return message_id, idempotency_key, outcome (delivered, not_delivered or unknown), and authoritative=true.
- Only an authoritative delivered result marks delivery complete. Only authoritative not_delivered evidence makes a message retryable; unknown, missing, mismatched or non-authoritative evidence leaves it blocked.
- Without a receiver-side reconciliation callback, uncertain messages remain blocked for explicit host/operator handling.
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
    authorize=host_guard_admission, deliver=agent_inbox,
    reconcile_delivery=host_receiver_lookup)
msg = bus.enqueue("manager", "developer", "TASK_REQUEST",
    {"task": "implement PDF encryption"}, idempotency_key="task-17-dev",
    thread_id="task-17")
bus.dispatch(msg.message_id)
# After a crash or ambiguous callback failure, reconcile before retry:
# bus.reconcile(msg.message_id)
# Developer proposes completion/review request; execution authority stays canonical.
```

Message kinds include query/response, task request/accept/reject, handoff,
status, blocked, review request/result, evidence submission and cancellation.
