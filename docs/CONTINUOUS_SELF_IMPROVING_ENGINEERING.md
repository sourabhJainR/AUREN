# Continuous Self-Improving Engineering

The portable runtime now treats engineering as a durable feedback system rather
than a sequence of isolated runs.

## Control loop

observe evidence -> predict risk -> decompose historical remediation -> execute
through the existing authority -> verify -> persist evidence/remediation ->
learn -> detect repeated failure patterns -> open bounded capability evolution.

## Guarantees

- Episode state is durable in the existing SQLite memory store.
- Restarted completed episodes are idempotent and do not execute again.
- Execution remains injected; this layer does not acquire repository mutation authority.
- Pre-execution failure gates remain active.
- Verification failure becomes persistent remediation and an evolution signal.
- Repeated unresolved outcomes can trigger the existing capability-invention gate.
- Every episode has an auditable state, iteration, plan digest, evidence IDs and remediation IDs.
- Terminal states are fail-closed: completed, escalated, or failed episodes are not silently rerun.
- Existing safety, review, sandbox, and approval boundaries remain authoritative.

## Productivity effect

The runtime reduces repeated diagnosis by carrying remediation forward, avoids
known-risk execution through historical prediction, makes successful work
reusable through persistent evidence, and turns recurring failures into bounded
self-improvement opportunities instead of ad-hoc fixes.

It is intentionally bounded: autonomous improvement is earned from verified
outcomes and never granted by a single successful run.
