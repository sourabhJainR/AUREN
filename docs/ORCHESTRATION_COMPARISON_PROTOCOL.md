# Framework-neutral orchestration contract and comparison protocol

AUREN should compare orchestration approaches using a common task, policy, budget, and evidence contract rather than framework-specific anecdotes. The portable contract in `portable.orchestration_spec` provides a deterministic workflow description and aggregates trial receipts from adapters.

## Workflow contract

The schema describes node identity/type/capability, retry budgets, side-effect idempotency, per-node timeouts, required evidence, entry nodes, edge conditions, policy digest, and global step/attempt/deadline budgets. Cyclic edges must declare a traversal limit. Retried side-effect nodes must be explicitly idempotent. The spec digest is stable across node/edge ordering and binds the policy digest.

An adapter for LangGraph, Microsoft Agent Framework, OpenAI Agents SDK, Google ADK, or another framework should:
1. Parse and validate the same `OrchestrationSpec`.
2. Map every declared capability to its host-enforced permission boundary.
3. preserve step/attempt/deadline budgets and checkpoint semantics;
4. emit the same `OrchestrationTrial` evidence fields for matched tasks;
5. fail closed when it cannot preserve a required policy, trace, checkpoint, or budget guarantee.

The spec is not a second workflow engine. It does not execute nodes or pretend that framework adapters have equivalent semantics; adapter conformance tests must prove the mapping.

## Comparison metrics

`compare_orchestration_trials` groups trial evidence by framework and reports sealed-holdout pass rate with a Wilson 95% interval, unauthorized actions, trace completeness, recovery success, repeated-run consistency, median duration, and mean cost. A framework with any unauthorized action is explicitly marked not policy-violation-free.

Fair comparisons require the same task distribution, policy bundle, compute budget, model/provider settings, timeout/retry budgets, and evidence requirements. Include unseen task families, long dependency chains, crashes and resume, stale/corrupt checkpoints, tool failures, repeated terminal errors, policy-denied actions, and replay attempts.

## Trust and limitations

The helper validates evidence structure and digest shape only. A caller-provided `sealed_holdout` label is not proof of a sealed corpus; hashes are not signatures; and local tests are not an independent campaign. The evaluator must independently control the corpus/oracle, authenticate the campaign attestation, and retain receipts outside the candidate runtime. Do not claim independence or security from a passing local comparison alone.

This protocol is informed by the Open Agent Specification work on cross-framework comparison, agent reliability metrics, trace-based assurance, and horizon-aware degradation studies. AUREN should reproduce any reported research result rather than assume a paper's findings transfer to its own runtime.
