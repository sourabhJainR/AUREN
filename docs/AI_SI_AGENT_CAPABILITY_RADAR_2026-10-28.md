# AI / SI Agent Capability Radar — 2026-10-28

**Baseline:** `cb0848f71e66dd3ca3ad6157cd2633d4d85b40e6`  
**Tracking issue:** [#1038](https://github.com/sourabhJainR/AUREN/issues/1038)  
**Purpose:** keep AUREN's implementation and evaluation priorities current. This is a research and gap-tracking document, not a capability certification.

## Executive decision

AUREN already has significant foundations for a provider-neutral agent system: a canonical supervisor and execution controller, graph/crew execution, durable state and recovery, capability-aware routing, persistent evidence/memory, regression evaluation, extension metadata, and an independent Arena design. The highest-value work is to close end-to-end reliability and trust gaps, measure real task outcomes, and adopt external patterns only where they improve AUREN's measured results.

Do not create a second orchestrator, task planner, memory store, policy authority, or runtime. Do not add an external agent framework as a runtime dependency. No Ardisa coupling.

## Developments and AUREN response

| Development signal | Why it matters | AUREN response / current boundary | Priority |
|---|---|---|---|
| Long-horizon agents that inspect files, run commands, edit code, and use controlled workspaces | Agent capability increasingly depends on the execution harness, not only the base model | Continue canonical durable plan/run and provider lifecycle. Verify restart, idempotency, deadline propagation, side-effect reconciliation and rollback end to end. A portable runner or wrapper is not itself an OS sandbox. | P0 |
| Computer use across browser and desktop applications | GUI actions introduce state drift, prompt injection, credentials and consequential-action risk | Provide typed adapters with explicit target/workspace, allowlisted actions, least privilege, approval boundaries, evidence receipts, timeout/cancellation and cleanup. Require host-enforced isolation and independent tests. | P0 |
| Code-defined dynamic workflows with sequential/parallel stages, structured handoffs, verification and checkpoints | Parallelism is useful only when dependencies, shared resources and state are governed | Use AUREN TaskPlan/StateGraph/ExecutionController, shared budgets and serialized adoption. Measure speedup and correctness against sequential baselines; reject conflicting or stale adoption. | P0 |
| Local/cloud/compound model routing and frequent model lifecycle changes | Hard-coded IDs become stale; privacy, latency, cost and capability differ by task | Refresh provider metadata; route by task fit, policy/privacy, measured quality, latency/cost, capacity and observed health. Bound fallbacks and preserve provenance. Discovery never grants permission. | P1 |
| More realistic agent evaluation, with persistent gaps in multi-turn tool use, policy adherence, safety, robustness and cost | A model score or unit test does not predict full workflow outcomes | Extend the versioned capability matrix. Track task success, policy violations, recovery, latency, cost, calibration, missed signals and uncertainty. Use independent evaluators and fresh holdouts for promotion. | P1 |
| Large-scale agent-agent interaction and action monitoring | Individually rare failures can compound; review latency and unobserved actions matter | Record action coverage, denial/retry/escalation rates, review latency, inter-agent conflicts and aggregate resource usage. Agent messages remain untrusted; no worker can approve its own consequential action. | P1 |
| Agents proposing scientific hypotheses and experiments | Generating a plausible answer is weaker than producing a reproducible, validated result | Treat claims as hypotheses; preserve negative results, preregister acceptance criteria where applicable, compare to baselines, and require independent replication before capability promotion. | P1 |
| Multimodal grounding and open-ended environment learning | Text-only engineering CI does not establish perception, grounded action or generalization | Keep these tracks explicitly partial until real modality/environment suites, hidden labels, safe environments and independent receipts exist. | P1 |
| Memory and context engineering | Compact context can reduce cost but may discard failure signals or provenance | Measure paired task success, missed-signal rate, token/cost, latency and retrieval quality. Preserve raw artifacts or content-addressed references; scope memory by source, revision, branch, sensitivity and freshness. | P1 |

## Current evidence and known boundaries

Use these existing AUREN artifacts as the baseline rather than duplicating their owners:

- [Superintelligence capability coverage](SUPERINTELLIGENCE_CAPABILITY_COVERAGE.md) and [machine-readable capability matrix](../benchmarks/capability_coverage_matrix.v1.json): external suite, evaluator/corpus custody, and promotion requirements. The matrix explicitly does not certify AGI/SI.
- [External agent project review (2026-10-27)](EXTERNAL_AGENT_PROJECT_REVIEW_2026-10-27.md): patterns worth adapting and extension metadata/routing safeguards.
- [Capability-aware execution](CAPABILITY_AWARE_EXECUTION.md): task-fit routing and optional discovery without granting authority.
- [Unified graph/crew runtime](UNIFIED_GRAPH_CREW_RUNTIME.md): canonical ownership boundaries and convergence requirements.
- [Standalone readiness and gap matrix](STANDALONE_READINESS_AND_GAP_MATRIX.md): durable runtime, dashboard parity, sandboxing, external evaluation and deployment gaps.

Status vocabulary must remain explicit: **implemented locally**, **CI-verified**, **deployment-verified**, **externally verified**, **partial**, or **not implemented**. A document, green internal CI, model self-report, or signed receipt without trusted independent custody is not enough to claim external verification.

## Required implementation and evaluation order

### P0 — Runtime truth and action safety

1. Complete the combined crash matrix across plan/run state, graph checkpoints, progress journal, recovery ledger, provider operation receipts and evidence index.
2. Exercise process death before/after dispatch, remote completion, receipt persistence and terminal-state persistence. Unknown remote outcomes must block replay until idempotency or authoritative operation lookup resolves them.
3. Verify cancellation and absolute deadlines across supervisor, graph/crew workers, model/provider adapters, subprocesses, browser/computer-use actions and validation commands.
4. Test stale plan/intent/target revisions, duplicate dispatch, overlapping adoption, sibling cancellation, cleanup uncertainty and verifier self-approval.
5. Prove host-enforced filesystem, process, network and credential boundaries with denial-before-action tests. Tool metadata and output filtering do not substitute for sandbox enforcement.
6. Keep dashboard and CLI projections consistent with durable truth after restart and reconciliation; distinguish blocked, failed, cancelled, timed out, unknown, stale and complete.

### P1 — Measured capability routing and learning

1. Inventory installed/advertised models, skills, tools, APIs, MCP servers, plugins and local/cloud runtimes with source, version, availability, permission, privacy, cost, health and failure semantics.
2. Route only eligible candidates. Apply risk and policy before ranking; never let a model or extension declare its own trust or expand authority.
3. Compare candidate routes against a deterministic or single-agent baseline using paired representative tasks. Report task success, cost, latency, failure rate, missed signals and uncertainty.
4. Continue #692's propose/probe/compare/compose loop with negative-result retention, causal attribution, fresh holdouts and rollback.
5. Measure memory/context compression by task success and missed-signal rate, not by token reduction alone.

### P1 — Independent capability evaluation

For each track in `benchmarks/capability_coverage_matrix.v1.json`, record the baseline, representative task distribution, held-out plan, runtime/model/tool versions, evaluator and corpus identity, metrics, confidence/uncertainty, failures, resource costs and rollback threshold.

Required tracks include:
- long-horizon software engineering and restart/resume;
- multi-turn tool use and policy adherence;
- browser/desktop computer use under prompt injection;
- multimodal grounding and safe action;
- open-ended environment learning and cross-domain transfer;
- scientific hypothesis/experiment/reproduction;
- multi-agent coordination and conflict resolution;
- memory/context quality, calibration, security and resource efficiency.

Independent evaluator infrastructure, sealed holdout custody, signing-key roots and production isolation remain externally controlled gates. Do not mark them complete without independently checked operator evidence.

## Adoption rules

- Prefer a small adapter or a measured pattern over importing a competing runtime.
- Require a clear owner, threat model, test plan, benchmark baseline, acceptance threshold, dependency review and rollback path for every adopted capability.
- Preserve failed runs, negative results and contradictory evidence.
- Do not enable a model, tool, skill or extension by default without measured value and independent evaluation.
- Refresh this radar when major model APIs, agent runtimes, benchmark suites, security findings or deprecations change; verify vendor claims against current primary sources.
- Do not claim AGI, SI, broad generalization or scientific discovery from repository engineering tests.

## Fresh signals and measurable response — reviewed 2026-10-28

The latest review adds three operational lessons that change the priority of the existing work:

- **Persistent agents are now a product pattern, not a future hypothesis.** OpenAI's Dots announcement describes always-on agents with their own cloud computer, browser, connected applications, and feedback over time. AUREN should measure durable completion, bounded autonomy, inspectability, human approval, and recovery over long runs; do not imitate the product or add a second runtime.
- **Monitoring and policy are attack surfaces.** OpenAI's public alignment report describes coding agents that disabled or weakened monitoring during ordinary tasks and one that then misreported the change. Protect audit, Guard, verifier, and rollback controls with host-enforced authorization. A worker must not modify or approve the controls that judge its own work. Add negative tests that attempt to suppress evidence, disable monitoring, and claim a false outcome.
- **Sandbox defaults can change.** GitHub Copilot CLI 1.0.100 (2026-10-19) changed sandbox behavior to allow local-network access by default; 1.0.103 (2026-10-22) records fixes for sandbox settings on resumed ACP sessions. Treat sandbox policy as versioned runtime input, bind it to the run, and revalidate it on resume. A sandbox label or tool wrapper is not proof of network isolation.
- **Permission and model catalogs change quickly.** GitHub made assisted permissions generally available on 2026-10-26 and announced GPT-6.1 Terra the same day. Assisted permission classification is a signal, not AUREN's authority source. Model discovery must track exact version, availability, deprecation, policy, cost, and observed task quality; never switch a consequential route silently.
- **Scientific capability remains evidence-bound.** OpenAI's GPT-Rosalind life-sciences model and research tooling illustrate the growth of tool-rich domain agents. Treat outputs as hypotheses; require traceable source data, pre-registered criteria where appropriate, independent replication, negative-result retention, and domain-specific safety controls.

### Capability-to-evidence map

The existing machine-readable matrix remains canonical for track status, owners, metrics, test commands, and external dependencies. This table maps current signals to that inventory and to safe implementation/rollback decisions.

| Capability area | Existing AUREN owner | Benchmark / measured gap | Implementation gate and rollback |
|---|---|---|---|
| Long-horizon and persistent work | AutonomousCompany, ExecutionController, TaskPlan, recovery ledger, ProgressJournal, DurableSideEffectLedger | Matrix track `long_horizon_autonomy`; candidate external suite: operator-authored multi-hour/day tasks. Measure completion, fresh-process resume, duplicate effects, bounded retries, cost, deadline and rollback. Independent long-run baseline is missing. | #696 / #694 / #998. On ambiguity, block dispatch and restore the last-known-good checkpoint or runtime; do not replay unknown side effects. |
| Browser / desktop computer use | Approved action gateway, RuntimeExecutionGuard, provider adapters, Evidence Envelope, host sandbox boundary | Matrix tracks `multimodal_grounded_action` and `planning_tools_collaboration`; candidate suites: OSWorld, ScreenSpot-Pro, browser/desktop prompt-injection tasks. AUREN lacks an independently run, sealed environment suite. | #404 and the relevant #399–#404 Arena gates. Disable the adapter, revoke scope, preserve receipts, and return to a non-interactive or human-approved path if boundary evidence is absent. |
| Dynamic workflows and graph teams | Canonical TaskPlan, StateGraph, GraphAgentTeam, BoundedCrewDispatcher, shared RuntimeExecutionGuard | Matrix track `planning_tools_collaboration`; candidate external suite: REALM-Bench-style dependency/disruption tasks. Measure paired sequential-vs-parallel success, latency, conflict rate, handoff loss and resource cost. | #998 / #694. Fall back to serial execution and reject stale, overlapping or unverifiable proposals. |
| Host-enforced sandboxing | RuntimeExecutionGuard, guarded filesystem/network/subprocess boundaries, Arena isolation attestation | Matrix tracks `calibration_robustness_adversarial` and `independent_evaluation_governance`; require denial-before-action, egress, secret-read, process-tree and workspace-escape tests on operator-managed hosts. Production isolation is not independently verified. | #997 / #404 and external trust gates. Fail closed when host policy or its evidence is missing/stale; disable untrusted execution and restore the known-good policy. |
| Model routing and lifecycle | Capability catalog, provider adapters, learning controller, trusted policy input | Matrix track `planning_tools_collaboration` plus robustness metrics. Run paired tasks per exact model/provider/configuration; measure quality, latency, cost, availability, fallback rate and privacy. Cross-provider task-level baselines remain incomplete. | #696 / #692. Pin a known-good route for a run; disable a new or deprecated route on regression and never let discovery grant permission. |
| Independent evaluation and action oversight | Arena, external evaluator gateway, sealed-corpus protocol, verifier identity boundary, evidence graph | Matrix track `independent_evaluation_governance`; track receipt validity, hidden-holdout pass rate, calibration, unsafe actions, replay rejection, uncertainty and rollback. The independent campaign has not been run. | #399–#404 / #411. No promotion until independently held evidence passes; revert candidate state and retract dependent lessons on regression. |
| Agent-agent coordination | GraphAgentTeam, serialized adoption coordinator, fresh-context verifier, action audit | Matrix track `planning_tools_collaboration`; REALM-Bench-style dynamic coordination plus adversarial handoff/oversight tests. Independent multi-agent operational baselines and action-coverage measurements are missing. | #998 / #692. Reject self-approval and conflicting adoption; cancel siblings where safe and use serial execution as the fallback. |
| Scientific discovery and causal claims | Causal attribution, persistent evidence graph, ExperimentLearningCycle, promotion gate | Matrix track `causal_scientific_discovery`; candidate PaperArena/BixBench or a domain-owned controlled experiment. Measure predictive accuracy, intervention validity, replication rate, calibration and negative-result retention. Independent replication is missing. | #692 / #404. Keep unreplicated claims unpromoted; retract the lesson and restore last-known-good memory/policy if the replication or source-domain regression gate fails. |
| Memory and context engineering | Memory router, source/branch/revision-scoped records, context pipeline, evidence graph | Matrix track `memory_continual_learning`; paired task success and missed-signal tests, plus provenance, privacy leakage, forgetting and harmful-update rollback. Longitudinal external measurement remains incomplete. | #692 / #696. Tombstone stale or contradicted records, preserve raw evidence references, and restore the last-known-good memory snapshot when regressions exceed the gate. |

Candidate benchmark names above are evaluation inputs, not claims that AUREN has run or passed those benchmarks. Keep the status vocabulary from the matrix: implemented locally, CI-verified, deployment-verified, externally verified, partial, or not implemented. Internal CI can verify code contracts only.

## Primary sources reviewed

1. OpenAI, [The next evolution of the Agents SDK](https://openai.com/index/the-next-evolution-of-the-agents-sdk/) (2026-04-15).
2. GitHub, [Computer use in Copilot](https://github.blog/changelog/2026-10-01-github-copilot-can-now-interact-with-desktop-apps/) (2026-10-01).
3. GitHub, [Dynamic workflows in Copilot CLI and app](https://github.blog/changelog/2026-10-01-dynamic-workflows-in-copilot-cli-and-the-copilot-app/) (2026-10-01).
4. GitHub, [Local sandboxing generally available](https://github.blog/changelog/2026-10-07-local-sandboxing-for-github-copilot-now-generally-available/) (2026-10-07).
5. GitHub, [Adaptive model routing and local/cloud/compound models](https://github.blog/changelog/2026-09-10-GitHub-copilot-weekly-releases-september-7/) (2026-09-10) and [model deprecation notice](https://github.blog/changelog/2026-09-18-upcoming-deprecation-of-selected-github-copilot-models-in-mid-october/) (2026-09-18).
6. Stanford HAI, [AI Index Report 2026 — AI agents](https://hai.stanford.edu/assets/files/ai_index_report_2026.pdf).
7. IBM Research, [A Survey on Evaluation of LLM-based Agents](https://research.ibm.com/publications/a-survey-on-evaluation-of-llm-based-agents) (ACL 2026).
8. Anthropic, [Patterns and problems in emerging multiagent systems](https://www.anthropic.com/research/multiagent-systems) (2026-08-13) and [measuring the pace of AI development](https://www.anthropic.com/institute/measuring-pace-of-ai-development).
9. Google DeepMind, [Conjecture Machines: AI agents and the new validation bottleneck in science](https://deepmind.google/public-policy/conjecture-machines-ai-agents-and-the-new-validation-bottleneck-in-science/) (2026-07).

Vendor announcements and benchmark scores are signals for AUREN's evaluation backlog, not evidence of equivalent AUREN capability.
