# Extension-pack value measurement and default activation gate

## Policy

No optional skill, MCP adapter, workflow pack, retrieval plugin, or other extension is enabled by default because it exists, has a manifest, appears in a benchmark, or is recommended by a model. The registry remains discovery-only. Installation, trust, policy approval, and activation are separate decisions owned by the existing Guard and action-governance paths.

Before a pack can be proposed for default enablement, compare the pack against the current no-pack baseline using the same pinned task set, task ordering, corpus digest, host evaluator, and runtime budget. Use at least 20 paired tasks and retain a per-task evidence reference for both runs. Record the exact baseline and candidate configuration digests, corpus digest, evaluator identity/version, provider/model version, success outcome, input-token count, latency, and safety violations. Missing evaluator outcomes are unknown, not failures or successes; missing evidence blocks the decision.

## Automated recommendation

`portable.extension_pack_value.evaluate_extension_pack_value` computes a recommendation only. It does not install, enable, grant trust, or alter policy. The current conservative default policy requires:

- At least 20 paired tasks over identical task IDs and a pinned corpus.
- At least a five-percentage-point task-success uplift.
- A positive paired-success signal whose 95% Wilson lower bound is above 0.5, with at least five discordant pairs.
- No candidate safety violations and no aggregate success-rate regression.
- Input-token cost no more than 10% above baseline and aggregate latency no more than 25% above baseline.
- Complete per-run evidence references and matching configuration/corpus digests.

These thresholds are a starting policy, not universal scientific constants. Tighten them for high-risk packs; do not relax them after seeing candidate results. Report all component metrics and failed gates, not only a pass/fail score. A passing result is a recommendation for a separately authorized decision, not activation authority.

## Evaluation protocol

1. Freeze the baseline configuration, candidate pack digest, task suite, corpus digest, and evaluator before running either arm.
2. Run baseline and candidate on the same tasks. Randomize or counterbalance order where environment effects are possible; isolate state so one arm cannot contaminate the other.
3. Capture evaluator-produced task outcomes and immutable raw/bounded artifacts. Context compression or token reduction alone is not proof of improved task quality.
4. Include negative security tasks: missing approval, stale artifacts, provider-scope mismatch, malformed policy, prompt injection, attempted secret access, and ambiguous side-effect outcomes. Any candidate safety violation blocks default enablement.
5. Repeat runs when variance or flakiness could change the result. Keep unscored and inconclusive results explicitly unknown.
6. Review cost, latency, failure class, regressions, task-family differences, and safety evidence. Do not aggregate away a high-risk regression.
7. Only after the recommendation passes may an authorized owner consider default enablement. Bind that action to the exact candidate digest, policy version, evidence bundle, and rollback target. Re-run the evaluation after any pack, provider, model, policy, corpus, or harness change.

## Rollback and status

The safe default is disabled. On regression, revoke the activation decision, restore the last-known-good configuration, retain both evidence sets, and re-run the baseline. Do not delete the pack or evidence needed to diagnose the regression.

Current status: measurement gate implemented; no pack has been activated by this gate. No live-provider uplift is claimed until host-evaluator runs produce the required paired evidence. Independent evaluator and sealed-holdout custody remain deployment-owned prerequisites, not facts established by unit tests.
