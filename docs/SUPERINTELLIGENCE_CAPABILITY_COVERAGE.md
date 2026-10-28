# Empirical Capability Coverage Matrix

**Status:** versioned roadmap and test inventory; not a capability certification.

This matrix tracks the empirical work needed before AUREN could support a credible claim of broad generalization. Current CI is primarily an internal engineering regression baseline. It does not establish multimodal grounding, open-ended learning, scientific discovery, or superintelligence.

The machine-readable source is [`benchmarks/capability_coverage_matrix.v1.json`](../benchmarks/capability_coverage_matrix.v1.json). Each track records its owner, dependencies, trusted data source, runtime/evaluator identity, metrics, current baseline, test command, held-out plan, threat model, CI gates, promotion criteria, and external dependencies.

## Reading status

- **`protocol_implemented_campaign_not_run`** — code/tests exist, but the independent campaign has not been run.
- **`protocol_and_ci_present`** — local protocol and CI coverage exist; external task validity and independent evidence remain unproven.
- **`partial_*`** — some runtime or security primitives exist, but the target capability has not been independently measured.
- **`not_implemented_external_suite_required`** — a modality- or domain-specific benchmark must still be implemented and run.

## Promotion rule

No track is promoted based on self-reported capability, code presence, a green internal fixture suite, or a digest without a trusted signature. A consequential promotion must be bound to exact-head CI, verified external evidence, an independent review, a last-known-good anchor, and a tested rollback. If evidence is missing, stale, contaminated, or not independently authenticated, promotion remains blocked.

## Critical external dependencies

AUREN maintainers can implement protocol and regression coverage, but cannot self-certify independent custody. Completion of the external campaign requires an independent operator to retain the sealed corpus/oracle and private signing key outside the candidate trust domain, run the evaluator on separately administered infrastructure, and publish signed aggregate results, baselines, uncertainty, failures, and limitations. Host isolation, organizational independence, and semantic novelty require operational evidence—not just signatures.

## Current repository evidence snapshot — 2026-10-28

Reviewed against baseline `main` SHA `5b3e3858d85d558945f2a4ccf65a18fe9b323ba7`. Its current-main check set is terminal: 28 successes, zero skipped checks, zero failures, and zero pending checks; all six push-triggered workflows completed successfully. The portable bundle and checksum artifacts were downloaded from [workflow run 40627642859](https://github.com/sourabhJainR/AUREN/actions/runs/40627642859) and verified: outer bundle artifact SHA-256 `f87dbad0bc9e6de7da9ea7c21226d4710e5281d797a8e3f0c289eb4d42ef56d8`, outer checksum artifact SHA-256 `9c3d195fed261c858d1475b7f4ba12977e32da9a24107da535c7ec6a7be989e0`, embedded ZIP SHA-256 `7772704781caf23c3b329d7fc5e76ee4492a9f8fd4a88b49140bbe61c1972be9`, and 508 manifest entries verified with no size/hash mismatches. The manifest pins this SHA, version `1.0.6`, and `target_repository_mutation=false`.

These results verify repository CI and artifact integrity only. All 28 current-main check-runs succeeded. Deployment behavior, independently controlled evaluator/oracle identity, sealed holdout custody, production host isolation, and external benchmark campaigns remain **not verified / blocked**. No AGI/SI or broad-generalization claim is supported. The graph recovery and runtime/learning/delivery regressions are internal evidence, not independent Arena results.

## Explicit non-claim

There is no numeric “superintelligence score” in this matrix. AUREN is being made compatible with rigorous evaluation, learning governance, transfer testing, and rollback. Whether it achieves broadly general or superintelligent performance remains an empirical question requiring independent validation.


## Living capability radar refresh — 2026-10-28

**Verified baseline:** `main` at `5b3e3858d85d558945f2a4ccf65a18fe9b323ba7` after PR #1046. All 28 current-main check-runs are terminal-successful and all six post-merge push workflows succeeded. The portable bundle and checksum artifacts are retained and their outer/inner digests plus all 508 manifest entries were verified. No local checkout was available for a local test run.

### External signals mapped to AUREN owners

| Signal and source | Existing AUREN owner | Measured gap and benchmark | Acceptance gate | Rollback |
|---|---|---|---|---|
| Long-running agents in cloud sandboxes: [Docker Cloud Sandboxes, 2026-09-24](https://www.docker.com/press-release/cloud-sandboxes-extending-secure-ai-agent-isolation-beyond-the-laptop/) and [Akamai Agent Sandbox, 2026-10-27](https://www.akamai.com/newsroom/press-release/akamai-launches-ai-inference-service-and-agent-execution-environment) | `ExecutionController`, recovery ledger, `RuntimeExecutionGuard`, workspace/provider lifecycle adapters | AUREN's durable execution and subprocess boundaries are not proof of production host isolation. Add adapter conformance for explicit workspace identity, least privilege, network/credential scope, bounded output, cancellation, cleanup, durable receipts, and resume. Measure crash/restart success, unauthorized access, cleanup failure, latency, and cost. | No host escape, secret access, or unauthorized egress in adversarial tests; unknown provider outcomes remain blocked; resume never duplicates a consequential operation. A separately administered isolation audit is still required. | Disable the candidate adapter and route to the last-known-good approved executor. Preserve checkpoints and receipts; require explicit reconciliation for unknown outcomes. |
| Code-defined dynamic workflows and desktop computer use: [GitHub dynamic workflows, 2026-10-01](https://github.blog/changelog/2026-10-01-dynamic-workflows-in-copilot-cli-and-the-copilot-app/) and [GitHub Copilot releases, 2026-10-02](https://github.blog/changelog/2026-10-02-github-copilot-weekly-releases-september-28/) | `TaskPlan`, `StateGraph`, Agent Team, `ExecutionController`, `ProgressJournal`, dashboard | Existing AUREN owners already model staged work, parallel dependencies, checkpoints, and human gates. Measure end-to-end task success, handoff loss, duplicate dispatch, action correctness, cancellation propagation, and resume after restart on pinned tasks. Computer-use support must stay an adapter behind host policy. | Paired single-agent/deterministic baseline; every consequential action is scoped and approved; all stages derive status from durable records; restart and cancellation tests show no replay or false completion. | Revert the adapter/workflow change and resume through the prior canonical graph path; block any action with an uncertain outcome. |
| Adaptive routing and model lifecycle: [GitHub model selection and releases, 2026-10-02](https://github.blog/changelog/2026-10-02-github-copilot-weekly-releases-september-28/), [model deprecations effective 2026-10-19](https://github.blog/changelog/2026-09-18-upcoming-deprecation-of-selected-github-copilot-models-in-mid-october/), and [Anthropic model catalog](https://www.anthropic.com/news/models) | `CapabilityFabric`, `AdaptiveRuntime`, provider health/lifecycle and fallback policy | Model IDs and availability change. Refresh from authoritative catalogs with bounded cache freshness; route by task capability, privacy, permissions, measured quality, latency, cost, resource capacity, and health. Test missing/retired IDs, rate limits, schema drift, fallback loops, and preserved budgets. | A stale ID cannot dispatch; fallback does not widen data scope, reset budgets, or skip approval; paired evaluation reports task success, latency, cost, and regression by task family. | Pin the last-known-good approved route, disable the stale ID, and return to deterministic/native fallback. |
| Multi-turn agent evaluation: [WildToolBench, ICLR 2026](https://yupeijei1997.github.io/WildToolBench/) and [customer-support skill executor benchmark, 2026-10-09](https://arxiv.org/abs/2610.14346) | Arena, evaluator gateway, evidence envelope, #692 learning/promotion gate | Public results show that realistic multi-step tool use remains difficult and judge choice can change verdicts. Add pinned multi-turn tasks, deterministic final-state and tool-trace checks, judge-agreement reporting, and negative-result retention. | Report per-domain task success, unauthorized tool use, missed signals, judge agreement, uncertainty, latency, cost, and regression. Independent evaluator and sealed holdout are required before promotion; internal CI is regression evidence only. | Reject promotion, retain failed/unscored evidence, restore the prior component digest, and rerun the pinned regression set. |
| Agent-agent coordination and action oversight: [GitHub's dynamic workflow design](https://github.blog/changelog/2026-10-01-dynamic-workflows-in-copilot-cli-and-the-copilot-app/) and the multi-agent research references in [#1038](https://github.com/sourabhJainR/AUREN/issues/1038) | `StateGraph`, Agent Team, `RuntimeExecutionGuard`, identity/authority policy, independent verifier | Measure coordination overhead, redundant work, communication cost, write conflicts, resource contention, escalation latency, and unauthorized-action rate against a single-agent or deterministic baseline. Agent messages remain untrusted input; a worker cannot approve its own consequential work. | Independent verifier identity/context, fresh evidence, serialized adoption, bounded shared budgets, and zero unresolved critical authority bypasses in the tested suite. | Disable parallel fan-out for the affected task class and use the sequential canonical path; preserve failed traces for analysis. |
| AI-assisted scientific discovery: [Anthropic science research index, including the 2026-10-16 quantum-circuit study](https://www.anthropic.com/science) | LearningSteward, causal attribution, experiment manifest, Arena and external replication owner | A model-generated hypothesis or an automated score is not independent scientific validation. Pin hypotheses, constraints, code/simulation, seeds, model/tool versions, and raw outputs; retain negative results. Measure constraint satisfaction, predictive validity, replication rate, calibration, cost, and source-domain regression. | Independent replication on a fresh dataset or environment; for consequential scientific claims, require domain-expert review and appropriate physical/experimental validation. | Do not promote the lesson or harness change; preserve the complete experiment lineage and restore the last-known-good digest. |

### Capability status vocabulary

- **Implemented:** the relevant AUREN code path exists.
- **CI-verified:** repository tests passed for the named contract on an exact revision. This does not imply production behavior.
- **Deployment-verified:** the capability was exercised in the intended deployment with retained operational evidence. Not established by repository CI.
- **Independently verified:** a separately controlled evaluator/corpus or host audit produced authentic evidence. Not established by this refresh.

AUREN's graph execution, durable journaling/recovery, evidence-linked verification, and selected runtime/learning/delivery integration have implementation and repository-test coverage. The newly tracked computer-use, cloud-sandbox, routing, coordination, and scientific-work signals are **candidate adoption areas**, not claims that AUREN already has equivalent capability. Production isolation, independent evaluator identity, sealed holdout custody, external campaign receipts, and operator-controlled migration/rollback remain blocked until separately evidenced.

### Promotion and rollback rule

Do not enable a new provider, computer-use adapter, skill pack, model route, or extension by default. Require a paired baseline/candidate run, task success and safety metrics, resource cost and latency, regression/transfer results, independent evaluation where promotion risk requires it, a pinned last-known-good digest, and a rehearsed rollback. Missing or stale evidence is not a pass. No AGI/SI or broad-generalization claim follows from this radar or internal CI.


### Latest signal update — 2026-10-28

Three additional host-policy and lifecycle signals sharpen the same gates:

- [GitHub local sandboxing reached general availability on 2026-10-07](https://github.blog/changelog/2026-10-07-local-sandboxing-for-github-copilot-now-generally-available/). Treat the local sandbox as a candidate host adapter; test denial-before-launch for filesystem, network, credential and local-tool access. Do not infer equivalent isolation from an API surface or policy file.
- [Assisted permissions reached general availability on 2026-10-26](https://github.blog/changelog/2026-10-26-github-copilot-assisted-permissions-generally-available/). Model-based risk classification may help UX, but it must never grant authority. AUREN's host policy, delegated scope, explicit approval and durable action receipt remain authoritative.
- [GPT-6.1 Terra became available on 2026-10-26](https://github.blog/changelog/2026-10-26-gpt-6-1-terra-in-github-copilot/), while another retirement wave is scheduled for [2026-11-16](https://github.blog/changelog/2026-10-15-upcoming-deprecation-of-selected-github-copilot-models-in-mid-november/). Model catalogs must detect missing/retired IDs and keep fallback bounded.
- [Centralized Copilot feature defaults are rolling out through 2026-10-26](https://github.blog/changelog/2026-10-22-global-default-for-copilot-features-generally-available/). This does not justify auto-enabling AUREN adapters or packs. Keep them opt-in unless paired value and independent evaluation support promotion.
- [Copilot CLI local-model discovery](https://github.blog/changelog/2026-10-07-discover-local-models-in-github-copilot-cli/) is discovery, not installation or trust. Check tool-calling/streaming capability, endpoint policy and health before routing; do not install or authorize local runtimes automatically.

Add targeted tests for permission spoofing, forbidden path/network access, retired model IDs, tool-schema mismatch, fallback-loop termination, and preservation of authority, privacy and shared budgets. These remain benchmark gates, not claims of deployed or independently verified behavior.
