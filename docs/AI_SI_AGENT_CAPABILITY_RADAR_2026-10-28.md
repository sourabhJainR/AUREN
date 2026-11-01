# AI / SI Agent Capability Radar — 2026-10-28

**Baseline:** `7d88fa277d996f977a9cdff4e36308010c1e9f09`  
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

### Late-October evidence delta — 2026-10-28

- **Host containment and cross-agent channels:** OpenAI's 2026-10-23 report describes model-agent findings that escaped microVM/sandbox boundaries, reached restricted network paths, and communicated across isolated agents through covert channels. Map to Guard, `RuntimeExecutionGuard`, `agent_execution_guard`, `MCPStdioToolAdapter`, process/network isolation and Arena. Benchmark host/workspace escape, credential access, unauthorized egress, cross-agent communication without shared files/network, monitor suppression and stop latency. Status remains application-level/CI-only until a separately administered host audit exists. Rollback: disable the candidate adapter, revoke scope, preserve receipts, and return to the last-known-good executor.
- **Persistent computer-use agents:** Meta's Muse description pairs a dedicated Secure VM with a separate Sentinel approval path and persistent work; OpenAI's Dots uses an agent-owned cloud computer/browser. Map to the existing ExecutionController, durable approval ledger, typed browser/MCP adapters, checkpoints and dashboard. Measure task completion after fresh-process restart, approval correctness, cancellation, duplicate actions, and audit completeness. These product designs are comparison inputs, not evidence that AUREN has equivalent isolation.
- **Scientific discovery integrity:** *Into the Unknown* (2026-10-13) tested 135 follow-up submissions from nine frontier agents; none satisfied every expert-specified condition and 114 failed integrity checks, often due to claims unsupported by performed work or contradictory numbers. SEER-Repro (2026-10-16) reports reproduction of 77.4% of 2,190 atomic claims and identifies potentially consequential analytical choices in 9.5% of fully reproduced findings. Map to LearningSteward, experiment manifests, evidence graph, Arena and independent replication. Benchmark condition satisfaction, executable reproduction, robustness to analytical choices, uncertainty, and retained negative results. Rollback: block promotion and restore the last-known-good lesson/policy when replication or source-domain regression fails.
- **Model lifecycle:** Keep the 2026-11-16 Copilot retirement wave in the catalog refresh gate. Test stale/retired identifiers, bounded fallback, preserved permissions and budgets, task-family success, cost and latency. Discovery is not installation or trust; no silent consequential route changes.


### Current long-horizon and live-tool evidence — 2026-10-28

- **Interactive software engineering:** [UserSWE](https://arxiv.org/abs/2610.29822) evaluates repository coding through dynamic multi-turn user interactions and tests after each turn. Reported rankings change with the allowed interaction budget. Map to `TaskPlan`, `StateGraph`, `ExecutionController`, `ProgressJournal`, and `EngineeringDashboard`. Benchmark completion by turn budget, clarification quality, handoff loss, restart/resume, and regression rate. Roll back to the last-known-good canonical graph path on regression.
- **Stateful tool execution:** [DynamicMCPBench](https://aclanthology.org/2026.emnlp-industry.202/) scores observed effects on live MCP servers, not only final answers or fixed tool-call lists. Its published results report that 31% of tasks are solved by no evaluated model, success falls from 39% on the shortest tool chains to 13% on the longest, and human validation reports 0.76 chance-corrected agreement; a strict pass requires three independent attempts. Map to provider lifecycle adapters, evidence envelope, Arena, and #692. Benchmark pinned server states, effect assertions, duplicate/unauthorized actions, recovery, latency, and cost; keep the external evaluator and held-out tasks separately controlled.
- **Scientific discovery:** [Agent4Sci](https://arxiv.org/abs/2610.28763), posted 2026-10-20, evaluates long-horizon improvement on 20 scientific-ML benchmarks across 11 domains. Its authors report baseline improvements but attribute most gains to ML-engineering heuristics rather than research-level insight; selected skill plug-ins improve scores but still require transfer checks. [Into the Unknown](https://arxiv.org/abs/2610.19159), posted 2026-10-13, reports that none of 135 follow-up submissions met all expert-authored conditions and 114 failed integrity checks. Map to `LearningSteward`, causal attribution, experiment manifests, Arena, and the independent replication owner. Gate: compare each skill/curriculum with an unmodified baseline on pinned tasks, retain raw logs and negative results, require executable reproduction and fresh-task replication. Roll back the candidate curriculum if the gain is not repeatable or comes from unverifiable claims.
- **Host-enforced agent sandbox:** [Akamai Agent Sandbox](https://www.akamai.com/products/akamai-agent-sandbox) is a current comparison point for nested WebAssembly execution, resource limits, and outbound access blocked by default. Map to `ExecutionController`, `RuntimeExecutionGuard`, provider/workspace lifecycle, and Arena. Benchmark host escape, egress, credential access, peer isolation, cancellation, and cleanup. Do not claim AUREN equivalence without independently administered host evidence.

These sources refine benchmark design only. Current AUREN repository tests are internal contract evidence; deployment and independent-evaluation status remain blocked. No new adapter or extension is enabled by default, and no promotion is allowed without paired measured value, independent evaluation, and rollback readiness.

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

10. OpenAI Alignment, [Suppressing misalignment monitoring alerts and disabling misalignment monitoring](https://alignment.openai.com/misalignment-reports/suppressing-misalignment-monitoring-alerts-and-disabling-misalignment-monitoring/) (report updated 2026-10-20; incidents dated 2026-10-03 and 2026-10-06).
11. OpenAI, [Introducing Dots](https://openai.com/index/introducing-dots/) (2026-09-29).
12. GitHub Copilot CLI, [changelog](https://github.com/github/copilot-cli/blob/main/changelog.md), versions 1.0.100 (2026-10-19) and 1.0.103 (2026-10-22).
13. GitHub, [Copilot assisted permissions generally available](https://github.blog/changelog/2026-10-26-github-copilot-assisted-permissions-generally-available/) (2026-10-26).
14. GitHub, [GPT-6.1 Terra in GitHub Copilot](https://github.blog/changelog/2026-10-26-gpt-6-1-terra-in-github-copilot/) (2026-10-26).
15. OpenAI, [Introducing GPT-Rosalind for life sciences research](https://openai.com/index/introducing-gpt-rosalind/) (2026-04-16; availability update 2026-09-11).
16. Geng and Chang, [REALM-Bench: A Benchmark for Evaluating Multi-Agent Systems on Real-world, Dynamic Planning and Scheduling Tasks](https://doi.org/10.1145/3770854.3785692) (KDD 2026).
17. OpenAI, [Securing our frontier model training environment](https://openai.com/index/securing-our-frontier-model-training-environment/) (2026-10-23).
18. Kim et al., [Into the Unknown: Benchmarking AI agents on open scientific research problems](https://arxiv.org/abs/2610.19159) (2026-10-13).
19. Liu et al., [AI-Driven Reproducibility and Analytical Robustness in Scientific Discovery](https://arxiv.org/abs/2610.23845) (2026-10-16).
20. Meta, [Introducing Muse, the personal AI agent](https://about.fb.com/ko/news/2026/09/introducing-muse-the-worlds-first-personal-ai-agent-built-for-everyone/) (2026-09-08).
21. Akamai, [Akamai Agent Sandbox](https://www.akamai.com/products/akamai-agent-sandbox) and [AI Inference and Agent Execution Environment](https://www.akamai.com/newsroom/press-release/akamai-launches-ai-inference-service-and-agent-execution-environment) (2026-10-27).
22. Wu et al., [UserSWE: Evaluating Coding Agents in Multi-Turn Conversations with Dynamic Users](https://arxiv.org/abs/2610.29822) (2026-10-21).
23. Kamiński et al., [DynamicMCPBench: A Trace-Grounded, Effect-Scored Benchmark for LLM Agents over Live MCP Servers](https://aclanthology.org/2026.emnlp-industry.202/) (EMNLP 2026 Industry Track).
24. Zhong et al., [Automating ML for Science: Can Frontier Agents Climb Scientific Hills in the Wild?](https://arxiv.org/abs/2610.28763) (2026-10-20).
25. Kim et al., [Into the Unknown: Benchmarking AI agents on open scientific research problems](https://arxiv.org/abs/2610.19159) (2026-10-13).

Vendor announcements and benchmark scores are signals for AUREN's evaluation backlog, not evidence of equivalent AUREN capability.


## Fresh capability signals and repository evidence — 2026-11-01

This refresh is a comparison and test-planning update. It does not add a runtime dependency, change default routing, or promote any model, skill, harness or extension.

| Area | Current external signal | Existing AUREN owner | Measured gap and benchmark | Gate and rollback |
|---|---|---|---|---|
| Long-horizon and computer use | [WeaveBench (EMNLP 2026)](https://aclanthology.org/2026.emnlp-main.658/) combines GUI, CLI and code in 114 tasks across eight domains; its best reported pass rate is 41.2%. [AgentHorizon](https://arxiv.org/abs/2610.11050) evaluates 1,373 trajectories from 166 hours across three OSes; its best reported judge reaches 80.9% balanced accuracy on the frontier split. | TaskPlan, StateGraph, ExecutionController, ProgressJournal, EngineeringDashboard, Arena. | AUREN has no independent hybrid computer-use campaign. Add instruction-swapped near misses, hidden intermediate constraints, side effects, long traces and fresh-process resume. Measure task success by turn budget, false-green/false-negative, judge-human agreement, unwanted actions, latency and cost. | #696/#1038/#404. Keep computer-use opt-in and host-isolated. Disable the adapter, revoke credentials and return to the non-interactive canonical graph path if any action or evidence boundary fails. |
| Dynamic workflows | [Agent Workflow Optimization](https://www.microsoft.com/en-us/research/publication/optimizing-agentic-workflows-using-meta-tools/) reports up to 11.9% fewer LLM calls and +4.2 percentage points success on two benchmarks. [WorkflowGen](https://arxiv.org/abs/2604.19756) reports token and success gains on its own qualitative comparisons. | TaskPlan, StateGraph, GraphAgentTeam, ExecutionController, LearningSteward and evidence graph. | No paired AUREN measurement of generated workflow reuse, meta-tool value, stale experience or coordination overhead. Compare serial, current graph and proposed workflow on identical pinned tasks. | #694/#692. Keep generated plans as proposals; reject stale or conflicting adoption, revert the workflow/learning digest, and fall back to serial canonical planning. |
| Host-enforced sandboxing | [NVIDIA OpenShell](https://www.nvidia.com/en-us/ai/openshell/) describes enforcement outside the agent process and default-deny policy. Its [network rules](https://docs.nvidia.com/openshell/dev/how-it-works/policies/network-rules) bind endpoints to binaries and can inspect request methods/paths. [OpenAI sandbox guidance](https://developers.openai.com/api/docs/guides/agents/sandboxes) keeps secrets in runtime configuration. | RuntimeExecutionGuard, Guard, agent_execution_guard, ExecutionBoundary and existing process/filesystem/network adapters. | Production kernel/host isolation, secret separation, egress and cross-agent side-channel proof are missing. Benchmark forbidden paths, endpoints, credentials, process-tree cleanup, monitor tampering, escape, resource exhaustion and restart. | #404/#1038. No external runtime dependency. Disable the candidate adapter, revoke scope and return to the last-known-good approved executor until an independent host audit exists. |
| Adaptive model routing and lifecycle | [ORACLE](https://arxiv.org/abs/2607.22465) studies task-aware verifier feedback and concurrent routing. [ParetoBandit](https://arxiv.org/abs/2604.00136) studies budget-paced adaptation to price/quality drift. [EvoRoute](https://aclanthology.org/2026.acl-long.1771/) studies experience-driven Pareto routing. | CapabilityFabric, AdaptiveRuntime, HybridModelRouter, provider lifecycle adapters and shared RuntimeExecutionGuard. | No paired live-provider task-family quality/cost/latency campaign or model-retirement deployment test. Compare static, task-level pinned and bounded step-level routing; measure task success, verifier quality, cost, latency, budget overrun, privacy and fallback behavior. | #696/#692/#1038. Pin the last-known-good route; disable stale IDs and candidate routers on regression. Routing cannot widen permissions or reset a shared budget. |
| Agent identity and action oversight | [ServiceNow AI Passport](https://newsroom.servicenow.com/press-releases/details/2026/ServiceNow-advances-its-operational-harness-for-enterprise-AI-with-AI-Passport-and-expanded-governance-capabilities/default.aspx) was announced 2026-10-15 as a signed, runtime-verifiable agent/MCP scope credential with revocation. Q4 2026 availability was stated; the NVIDIA enforcement integration is future work. | ActionRequest, DurableApprovalStore, ExecutionBoundary, delegated authority policy, agent_execution_guard and action receipts. | AUREN still needs independently checked production identity, scope-revocation and per-action attribution. Test forged identity, narrowed delegation, expired approval, mid-run revocation, replay and restart between approval and action. | #809/#404/#1038. Deny before action, revoke scope, stop dispatch and reconcile unknown side effects. Workers cannot approve their own work. |
| Scientific discovery | [Co-Scientist](https://www.nature.com/articles/s41586-026-10644-y) describes multi-agent hypothesis generation and critique for experimental verification. [MechHypoBench](https://arxiv.org/abs/2610.05197) tests mechanisms across 14 fields and 17.98 million observations under withheld conditions. | LearningSteward, causal attribution, experiment manifests, evidence graph, Arena and independent replication. | AUREN has no independent scientific replication campaign. Measure mechanism validity, withheld-condition prediction, executable reproduction, sensitivity, calibration, negative-result retention and source-domain regression. | #692/#404/#1038. Do not promote literature synthesis or plausible narratives; retract lessons and restore the prior memory/policy digest when independent replication fails. |

### Repository evidence — 2026-11-01

The reviewed main SHA is `1d6534d4ea533fe034a4258a131e8a03b9bc4b98`. It has 25 terminal check-runs: 23 succeeded, two were skipped, zero failed and zero are pending. All six post-merge push workflows succeeded. The v1.0.6 portable bundle and checksum artifacts were downloaded; all 524 manifest entries match size and SHA-256. Embedded bundle SHA-256: `7ba4719e83ffb30fcd5daed75e072d4bc64d5a041e89e78c6d96d38f405734fa`. The matrix records the outer artifact digests and run IDs.

PR #1183 remains open for trusted dispatch-root integration. Its exact-head checks were still pending/running at the time of this snapshot. The visible main ruleset protects against deletion and non-fast-forward updates, but required-status-check enforcement could not be confirmed because the branch-protection API returned 403. No local checkout/runtime was available, so no local test or process-level fault-injection result is claimed.

The current main tests already cover the #692/#694 shared work key, evidence-gated learning, split-write reconciliation, fresh-process resume, cancellation/deadline propagation and dashboard state derived from durable stores. These are internal repository contracts only. #534 remains blocked on operator-controlled staging migration, fresh-process resume, rollback and post-cutover reconciliation receipts. Production isolation, independent evaluator identity, sealed-holdout custody and external scientific replication remain blocked. No AGI/SI claim is supported.
