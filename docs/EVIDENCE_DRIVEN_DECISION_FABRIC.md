# Evidence-Driven Decision Fabric

Phase 23 turns the persistent evidence graph into an active planning input.

## Contract

EvidenceDrivenDecisionFabric reads only evidence nodes linked to the capability
root that are:

- explicitly marked as decision observations;
- verified;
- uncontaminated;
- from the exact requested task family;
- backed by a candidate provider/tool route.

It derives confidence from observation count and aggregates success, quality,
duration, and cost. Conflicting verified observations fail closed into a
conservative plan rather than silently averaging disagreement.

The resulting EvidenceDecisionPlan adapts:

- provider/tool route;
- verification depth;
- retry budget;
- escalation path;
- parallel execution;
- local/cloud resource lane;
- duration and memory budgets.

The plan is content-addressed by decision_digest.

## Trust boundary

This module is policy-only. It does not execute tools, mutate capabilities,
promote lifecycle state, or alter the evidence graph. Execution and promotion
remain owned by their existing authority boundaries.

Cold start or insufficient evidence uses a conservative deep-verification
fallback. Evidence from another task family, contaminated evidence, and
unverified observations cannot steer routing.

## Evidence shape

A linked observation node uses metadata such as:

kind=decision-observation, capability, task_family, provider, tool_path,
success, quality, duration, cost, verified, and contaminated.

This deliberately keeps the graph generic while making the decision fabric
an evidence consumer rather than another source of truth.
