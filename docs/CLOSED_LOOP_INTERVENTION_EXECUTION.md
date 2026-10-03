# Closed-Loop Intervention Execution Boundary

Phase 30 creates an explicit boundary between a learning recommendation and
actual mutation.

## Required controls

1. The learning decision must be executable.
2. An external approval token must bind the exact decision digest and
   intervention digest.
3. The executor may apply only that exact intervention.
4. Verification is mandatory after application.
5. Failed verification invokes rollback.
6. Every receipt records whether rollback occurred and preserves evidence IDs.
7. Promotion is always blocked by this component; lifecycle promotion remains a
   separate gate.

The executor receives callbacks rather than importing a capability registry or
provider implementation. This keeps the boundary usable with local, remote, or
future execution substrates without creating a hard dependency.

## Security invariant

A caller cannot turn a stale or modified intervention into an executable one by
reusing an approval token: the token's bound digests must match the immutable
decision and intervention.

The next phase can connect this receipt to the persistent evidence graph and
fresh-holdout campaign runner.
