# Evidence-Bound Capability Lifecycle Gate

This gate connects the causal promotion evidence chain to the existing bounded
capability canary lifecycle without taking lifecycle mutation authority.

Promotion requires:
- causal promotion eligibility;
- matching capability identity;
- at least three bounded canaries;
- every canary passing the lifecycle safety threshold;
- fresh independent holdout evidence;
- no contamination.

The result is a recommendation only. The host/orchestrator remains responsible
for starting canaries, changing lifecycle state, deployment, and rollback.

Pipeline:

validation -> experiment -> causal gate -> bounded canary -> lifecycle gate -> promote/hold
