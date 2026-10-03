# Long-Horizon Campaign Manager

This phase adds bounded multi-step pursuit with verified progress,
failure-aware replanning, interruption, and checkpoint receipts.

Hard invariants:
- a step is complete only after verification;
- every step consumes a finite budget;
- replanning is bounded and cannot reuse completed/failed IDs;
- a failed step remains in the evidence trail even if a repair step succeeds;
- interruption prevents an accepted terminal result;
- digests make checkpoints and outcomes replayable.

Execution is injected, so the manager does not hard-code a provider, tool,
model, or environment dependency.
