# Framework-neutral agent interoperability contract

AUREN can integrate specialist crews from different orchestration frameworks without
mistaking framework compatibility for safety or reliability. The contract in
`portable/agent_interoperability.py` normalizes workflow intent and runtime
observations so candidate adapters can be evaluated against the same benchmark
digest and the same hard gates.

## What the contract captures

- Agent identities, roles, capabilities, typed input/output field declarations and
  maximum handoffs.
- Workflow edges, explicit action allowlists, required evidence, human-approval
  actions, maximum steps/attempts, deadline and cost budget.
- Adapter observations for case outcomes, sealed holdouts, authorization,
  bounded termination, recovery, evidence references, unauthorized actions,
  runtime errors, elapsed time and cost.
- Common acceptance rules and a deterministic comparison ordering.

## Minimal usage

```python
from portable.agent_interoperability import (
    AgentContract, WorkflowContract, AdapterObservation, compare_adapters,
)

workflow = WorkflowContract(
    workflow_id="coding-change",
    agents=(
        AgentContract("builder", "implement", ("repository.write",)),
        AgentContract("reviewer", "review", ("repository.read",)),
    ),
    edges=(("builder", "reviewer"),),
    allowed_actions=("read", "edit", "test"),
    required_evidence=("trace", "verification", "artifact_digest"),
    max_steps=40,
    max_attempts=5,
    deadline_seconds=600,
    max_cost_units=25,
)
sealed_digest = "digest-issued-by-the-independent-evaluator"
observations = [
    AdapterObservation(
        adapter="adapter-name",
        benchmark_digest=sealed_digest,
        case_ids=("case-1", "holdout-1"),
        passed_case_ids=("case-1", "holdout-1"),
        holdout_case_ids=("holdout-1",),
        passed_holdout_case_ids=("holdout-1",),
        authorization_enforced=True,
        bounded_termination=True,
        recovery_verified=True,
        evidence_refs=("trace", "verification", "artifact_digest"),
        duration_seconds=42,
        cost_units=2.4,
    ),
]
results = compare_adapters(
    workflow, observations, expected_benchmark_digest=sealed_digest
)
```

The example illustrates the adapter shape only; it is not evidence that any
particular framework has passed. An adapter must derive these fields from actual
runtime receipts and independent evaluator output. Never set a boolean to true
because the framework documentation claims the feature exists.

## Promotion rules

An adapter is rejected for any benchmark mismatch, missing required evidence,
unauthorized action, unbounded termination, failed recovery gate, runtime errors,
missing holdouts, insufficient overall/holdout pass rate, deadline breach or cost
budget breach. The numeric score only orders candidates after the hard-gate result;
a high score cannot override a failed safety gate.

The same sealed benchmark digest must be used across candidates. Keep evaluator
and corpus custody independent from the agent under test. A local hash/digest
does not prove the evaluator was independent, the corpus was sealed, the sandbox
was isolated, or a receipt was signed by a trusted party. Those require separately
verified external custody, isolation and attestation evidence.

## Rollout plan

1. Implement an adapter that converts a framework run into `AdapterObservation`.
2. Run identical public smoke cases, then independently custodied sealed holdouts.
3. Test crash/restart, duplicate handoffs, timeout, missing evidence, policy bypass
   attempts and long-horizon degradation.
4. Publish normalized receipts and paired cost/latency/reliability results.
5. Promote an adapter only after hard gates and AUREN's existing Arena promotion
   and rollback controls pass. Keep unverified adapters opt-in.

This module is an interoperability and comparison contract. It does not execute
agents, grant authority, provide sandbox isolation, or replace the Independent
Generalization Arena.
