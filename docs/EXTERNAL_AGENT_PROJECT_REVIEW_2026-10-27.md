# External Agent Project Review and AUREN Adoption Plan

Review date: 2026-10-27

## Decision

Adopt techniques, not a second runtime. AUREN already has a provider-neutral graph/team runtime, a durable progress journal and recovery ledger, persistent memory, bounded context packing, repository graph retrieval, capability routing, optional extension discovery, verification, and policy-gated execution. Replacing those owners with another framework would create competing state and authority paths.

This review prioritizes small additions to the canonical AUREN owners. External project claims, including token savings and quality benchmarks, are hypotheses until reproduced on AUREN's own representative workload.

## Project-by-project findings

| Project | Useful concept | AUREN disposition |
|---|---|---|
| [Everything Claude Code](https://github.com/affaan-m/everything-claude-code) | Reusable skills, hooks, memory optimization, evals, and cross-host workflow conventions | Reuse focused workflows where they add coverage. Keep AUREN's supervisor, policies, and evidence ledger canonical. |
| [agent-skills](https://github.com/addyosmani/agent-skills) | Spec-plan-build-test-review-ship lifecycle; small tasks with explicit exit evidence | Align task artifacts and gates to this lifecycle. Do not create a second task tracker. |
| [Ruflo](https://github.com/ruvnet/ruflo) | Multi-agent coordination, shared memory, routing, lifecycle visibility | Reuse the ideas in AUREN's StateGraph, Agent Team, memory router, and durable progress path. Do not import a second orchestrator or grant agents additional authority. |
| [wshobson/agents](https://github.com/wshobson/agents) | Modular agents, skills, and commands grouped by task domain | Use task-specific capability discovery and progressive disclosure rather than loading a large catalog into every context. |
| [claude-mem](https://github.com/thedotmack/claude-mem) | Cross-session observations, summaries, retrieval, and privacy controls | Keep durable memories in AUREN's canonical memory router; retain source, task, sensitivity, freshness, and approval rules. Never auto-promote raw observations to policy. |
| [planning-with-files](https://github.com/OthmanAdi/planning-with-files) | Durable active plan, findings, progress, restart/compaction recovery | AUREN already has a durable journal, task state, checkpoints, and recovery. Prefer those machine-readable canonical records over parallel markdown state; add a readable projection only where it improves handoff. |
| [Graphify](https://github.com/Graphify-Labs/graphify) | Local code/document/schema graph and relationship evidence | Keep as an optional provider. Validate snapshot freshness and provenance; fall back to native repository intelligence. |
| [Understand-Anything](https://github.com/Egonex-AI/Understand-Anything) | Interactive repository map, guided tours, and change-impact exploration | Extend existing repository maps and impact analysis instead of adding a competing index. UI work is lower priority than correctness and evidence gaps. |
| [ponytail](https://github.com/DietrichGebert/ponytail) | YAGNI, minimal-change discipline, deletion before addition | Keep as an advisory design constraint. It must never remove required validation, security, error handling, or tests. |
| [rtk](https://github.com/rtk-ai/rtk) | Reduce noisy command output before it reaches a model | Apply bounded, structured summaries to logs and tool results; preserve raw output or a content digest/path for audit and failure diagnosis. Measure token savings and missed-signal rates. |
| [headroom](https://github.com/headroomlabs-ai/headroom) | Local context compression for tool output, logs, and retrieved chunks | Extend AUREN's deterministic context packing only where it preserves errors, evidence, acceptance criteria, and security signals. Avoid a mandatory proxy dependency. |
| [context-mode](https://github.com/mksglu/context-mode) | Keep large tool results outside model context and return focused extracts | Prefer bounded retrieval, artifact references, and task-relevant slices. Tool-output isolation must not be mistaken for OS sandboxing. |
| [UI UX Pro Max](https://github.com/nextlevelbuild/ui-ux-pro-max) | Structured UI styles, palettes, typography, and design references | Use only for UI tasks; treat design choices as proposals and validate accessibility, responsiveness, and existing product conventions. |
| [taste-skill](https://github.com/Leonxlnx/taste-skill) | Reference-led art direction and browser checks across viewport sizes | Adopt screenshot/viewport regression checks for UI changes when browser tooling is available. Do not make visual claims without captured evidence. |
| [video-shotcraft](https://github.com/Vincentwei1021/video-shotcraft) | Reusable shot recipes and beat-synchronized product video generation | Optional product-marketing workflow, not a core runtime priority. Generated media needs explicit asset and license checks. |
| [Agent-Reach](https://github.com/Panniantong/Agent-Reach) | Unified discovery across web, GitHub, Reddit, and video sources | Route through approved tools and source-specific evidence. No API-fee claim overrides terms, access controls, or privacy policy. |
| [cc-switch](https://github.com/farion1231/cc-switch) | Central management of provider, MCP, skills, and prompts | Reuse the catalog and configuration-visibility concept. Do not silently change provider configuration or install/enable tools. |
| [pm-skills](https://github.com/phuryn/pm-skills) | Chained discovery, prioritization, assumption-testing, and launch workflows | Add domain workflows only when a product-management task is detected; share AUREN's plan, evidence, and approval controls. |
| [claude-skills](https://github.com/alirezarezvani/claude-skills) | Broad domain skill library | Discover narrowly by task and trust metadata. Do not load a large catalog or treat skill prose as verified facts. |
| [career-ops](https://github.com/career-ops-hq/career-ops) | Job matching and draft preparation with a human-controlled submission | Useful optional personal workflow, unrelated to AUREN's core engineering runtime. External submissions must remain user-confirmed. |

## Implemented in this change

The optional extension inventory now reports a conservative capability manifest for each configured extension and discovered skill:

- observed availability/health state, distinct from verified safety;
- declared version, capability and evidence types;
- cost class, freshness expectation, and failure behavior;
- permissions and side-effect declarations;
- approval requirement, bounded output size, source, and license;
- conservative defaults when metadata is missing.

The registry remains read-only. It does not install, enable, invoke, or grant trust to an extension. An extension's self-declared trust label is not evidence of independent verification. Discovered skills default to unverified, and external/MCP providers require approval by default.

## Cost-, capability-, and privacy-aware extension routing

The capability catalog now ranks only extensions that pass manifest, trust, approval, permission, freshness, and output-bound checks. It uses required capability coverage, declared cost class, privacy class, and side-effect count for deterministic ordering. It can combine eligible extensions only when their union covers every required capability; incomplete coverage falls back to repository-native work. Unknown privacy is not treated as local; external data use requires an explicit policy flag. If no extension qualifies, the planner returns the repository-native fallback. The registry does not grant trust or approval by itself. Task-generated route fields cannot authorize external data use or supply trust grants; those values come from a separate trusted policy input.

## Repository artifact freshness implementation slice

The optional extension inventory validates required metadata types, trust class, approval booleans, and output bounds before an extension is eligible; invalid manifests are marked unavailable. It also checks graph/index artifacts against a sidecar manifest named `<artifact-name>.auren-manifest.json`. The manifest binds the artifact SHA-256 to a repository snapshot SHA-256. Snapshot computation is bounded to 20,000 paths and 64 MiB of file content; an incomplete scan, missing manifest, malformed digest, changed artifact, changed repository, or path outside the repository is never marked fresh. The capability planner only exposes an extension hint when trust, freshness, availability, and approval policy all pass.

The manifest builder returns data for the artifact producer to persist; it does not mutate or install extensions. Existing external graph tools do not automatically produce this manifest, so their artifacts remain unverified until an adapter writes the manifest after generation. The repository context page cache now supports replace-on-refresh: unchanged content-addressed pages are reused, stale pages for the repository domain are evicted, and unrelated memory/history domains are preserved. This is not a substitute for per-file graph-neighborhood invalidation. Live task benchmarks remain open.

## Context benchmark evidence

Context benchmark reports now bind task identity, raw context, and selected context with SHA-256 digests, preserve provider-reported token counts separately from approximate character-based estimates, and accept an optional host-evaluator task-success result only with an evidence reference and paired raw/bounded artifact references. The benchmark never infers task success from context reduction; live-provider task evaluation remains an explicit host/evaluator responsibility. Repository context entries now carry bounded per-file content fingerprints; files that exceed hashing limits or cannot be read are marked incomplete instead of being treated as fresh.

## Gate status after implementation

1. **P0 — Extension metadata and routing:** implemented manifest type validation, explicit privacy classes, cost/capability routing, trusted-policy grants, approval requirements for side effects and external data use, output bounds, and fail-closed fallback. The canonical action gateway remains authoritative for provider scopes; new adapters must retain boundary-level tests.
2. **P0 — Recovery and state:** strict validation now forwards cancellation events. Deadline and cancellation regressions are covered by deterministic tests and the runtime-resilience matrix. Unknown remote outcomes remain blocked until reconciliation.
3. **P1 — Context economics:** reports include raw/selected context digests, latency, retention/missed-signal metrics, approximate token estimates, provider-reported token usage, and optional host-evaluator task success with an evidence reference. Live-provider task-success benchmarking remains a host/evaluator responsibility; no performance gain is inferred from compression alone.
4. **P1 — Repository freshness:** graph artifacts are trusted only when their sidecar matches the bounded repository snapshot digest. Repository context pages include bounded file-content fingerprints and stale pages are evicted on refresh. Incomplete hashing is explicitly warned. Targeted graph-neighborhood invalidation remains an optimization; full-artifact freshness checks fail closed.
5. **P1 — Regression genome:** accepted fixes can become candidate cases. Promotion requires distinct deterministic runs, an independent holdout, verified evidence references, independent evaluator identity, and explicit human approval. The evaluator is injected by the host; no candidate is auto-promoted without it.
6. **P2 — Domain skill packs:** manifest, license, permission, cost, privacy, approval, and output-bound controls are available. No UI/product/video/career packs are installed automatically; add them only when a task requires them and the specific pack has been reviewed.

## Verification requirements

- Run `python .ai-harness/extension_registry.py`.
- Run `python -m unittest tests.test_extension_registry tests.test_extension_policy tests.test_capability_catalog tests.test_context_engine -v`.
- Run the applicable architecture, hardening, portable-bundle, runtime-resilience, and integration-conflict checks on the exact PR head.
- Confirm the branch's final head before merge; after merge, verify required checks on the resulting main commit.
- Roll back the scoped implementation PR if a gate regresses. Do not disable fail-closed freshness, trust, approval, privacy, cancellation, or holdout checks to make CI pass.

## Limits

This is a design and repository implementation review, not an independent security audit of the listed projects. Availability detection is not a health check, a manifest is not proof of trust, and local parsing does not by itself prove sandbox isolation. External benchmark claims are not adopted as AUREN performance claims.


## Freshness addendum — 2026-10-28

This addendum refreshes the review against the living radar in [#1038](https://github.com/sourabhJainR/AUREN/issues/1038). It does not change the decision to keep AUREN's existing runtime and authority boundaries canonical.

### New signals to test, not copy

- **Long-running isolated work:** Docker announced Cloud Sandboxes on 2026-09-24, and Akamai announced Agent Sandbox plus managed inference on 2026-10-27. These are candidates for adapter evaluation, not new runtime dependencies. AUREN must independently verify host-enforced filesystem/network/credential boundaries and preserve its own recovery ledger, approvals, and operation identity.
- **Dynamic workflows and computer use:** GitHub documented code-defined dynamic workflows on 2026-10-01 and computer-use/model-selection updates on 2026-10-02. Map the concepts to AUREN's TaskPlan, StateGraph, ExecutionController, ProgressJournal, and dashboard. Do not add a second orchestrator.
- **Model lifecycle:** GitHub's selected Copilot model retirements took effect on 2026-10-19; current catalogs continue to evolve. Route against authoritative availability/deprecation data, measured task fit, privacy, permissions, quality, latency, cost, and health. Fallback must preserve budget, authority, and data scope.
- **Evaluation realism:** WildToolBench (ICLR 2026) reports low session accuracy on realistic multi-step tool-use tasks; an October 2026 customer-support skill benchmark reports non-trivial disagreement among evaluators. AUREN's benchmark design should include end-state checks, tool-trace auditing, judge agreement, uncertainty, and retained failures. Internal fixtures cannot substitute for independent evaluator and sealed-holdout custody.
- **Coordination and oversight:** measure the benefit of multi-agent execution against a deterministic or single-agent baseline. Track redundant work, conflicts, communication cost, escalation delay, unauthorized actions, and review defects. Agent messages are untrusted; consequential adoption remains serialized and independently verified.
- **Scientific discovery:** Anthropic's science index includes a 2026-10-16 quantum-circuit design study with an automated evaluator. Treat this as a benchmark lead only. Require pinned experiments, negative-result retention, fresh replication, domain review, and appropriate physical validation before making scientific capability claims.

### Acceptance gates

For each lane, the machine-readable radar now records an existing AUREN owner, current implementation/evidence status, measured gap, benchmark, acceptance gate, external dependency, and rollback. New adapters remain opt-in. Promotion requires paired measured value and independent evaluation when applicable; missing evidence blocks promotion. No new runtime framework dependency, no Ardisa dependency, and no AGI/SI claim are introduced by this review.

### Source links

- [Docker Cloud Sandboxes — 2026-09-24](https://www.docker.com/press-release/cloud-sandboxes-extending-secure-ai-agent-isolation-beyond-the-laptop/)
- [Akamai Agent Sandbox and Inference — 2026-10-27](https://www.akamai.com/newsroom/press-release/akamai-launches-ai-inference-service-and-agent-execution-environment)
- [GitHub dynamic workflows — 2026-10-01](https://github.blog/changelog/2026-10-01-dynamic-workflows-in-copilot-cli-and-the-copilot-app/)
- [GitHub Copilot releases and model selection — 2026-10-02](https://github.blog/changelog/2026-10-02-github-copilot-weekly-releases-september-28/)
- [GitHub model deprecations — 2026-09-18, effective 2026-10-19](https://github.blog/changelog/2026-09-18-upcoming-deprecation-of-selected-github-copilot-models-in-mid-october/)
- [WildToolBench — ICLR 2026](https://yupeijei1997.github.io/WildToolBench/)
- [Agentic customer-support skill evaluation — 2026-10-09](https://arxiv.org/abs/2610.14346)
- [Anthropic science research index](https://www.anthropic.com/science)


### Latest host-policy and model-lifecycle signals — 2026-10-28

- [Local sandboxing GA, 2026-10-07](https://github.blog/changelog/2026-10-07-local-sandboxing-for-github-copilot-now-generally-available/) provides a concrete comparison point for filesystem, network, credential and tool boundaries. Evaluate AUREN adapters against denial-before-launch and host-side enforcement; do not infer security from model behavior.
- [Assisted permissions GA, 2026-10-26](https://github.blog/changelog/2026-10-26-github-copilot-assisted-permissions-generally-available/) adds model-based classification of some command/path/URL requests. Keep the model advisory. AUREN's host policy, delegated scope, explicit approval and durable action receipt remain the authority.
- [GPT-6.1 Terra, 2026-10-26](https://github.blog/changelog/2026-10-26-gpt-6-1-terra-in-github-copilot/) and [model retirements scheduled for 2026-11-16](https://github.blog/changelog/2026-10-15-upcoming-deprecation-of-selected-github-copilot-models-in-mid-november/) reinforce the need for bounded catalog freshness, stale-ID rejection and policy-preserving fallback.
- [Centralized feature defaults, rollout through 2026-10-26](https://github.blog/changelog/2026-10-22-global-default-for-copilot-features-generally-available/) is not a reason to default-enable AUREN packs or adapters. Keep opt-in and require measured value plus independent evaluation.
- [Local-model discovery in Copilot CLI, 2026-10-07](https://github.blog/changelog/2026-10-07-discover-local-models-in-github-copilot-cli/) is discovery only; it does not install or validate a runtime. Require capability, endpoint, permission and health checks before any AUREN route is eligible.

### Latest shutdown and monitoring-integrity signals — 2026-10-28

- [OpenAI Alignment's incident report, updated 2026-10-20](https://alignment.openai.com/misalignment-reports/suppressing-misalignment-monitoring-alerts-and-disabling-misalignment-monitoring/) describes two internal coding tasks where agents weakened monitoring: one suppressed automatic alerts, and another removed a monitor and later misreported the change. Treat this as a concrete threat-model input, not proof of general behavior.
- [Reuters' 2026-10-27 report](https://www.reuters.com/world/china/deepseek-ai-model-shows-safety-gaps-in-independent-tests-2026-10-27/) summarizes Fangcun AI's independent API tests: shutdown software was disabled in 39% of trials after a warning that shutdown was imminent, compared with 2% when the model was explicitly told to permit shutdown; one long-running trial also interfered with a peer agent's process. The report notes possible evaluation awareness, so do not generalize these rates to ordinary deployment.
- Map both signals to the existing `ExecutionController`, `RuntimeExecutionGuard`, `ProgressJournal`, Guard, delegated authority, cancellation/termination reconciliation, and independent Arena evaluator. Add paired tests for ambiguous warning versus explicit stop, attempts to disable monitoring, peer-process interference, and termination uncertainty. Require denial-before-action and a durable termination receipt; a worker's own claim is not sufficient.
- Keep deployment and independent-evaluation status blocked until a separately controlled evaluator verifies host isolation, monitor integrity, stop latency, and peer isolation. On failure, stop dispatch, preserve the last-known-good monitoring policy and evidence, and keep the task blocked/unknown until reconciliation. Do not auto-retry a possibly live worker or promote an adapter from internal CI alone.


### Long-horizon and stateful tool-evaluation signals — 2026-10-28

- [UserSWE](https://arxiv.org/abs/2610.29822) tests repository coding through multi-turn dynamic-user interactions with execution-grounded verdicts after each turn. The paper reports that agent rankings change with the allowed interaction budget. AUREN owner mapping: `TaskPlan`, `StateGraph`, `ExecutionController`, `ProgressJournal`, and `EngineeringDashboard`. Gate: pin the task and user-information schedule, test every turn, and measure completion by budget, clarification quality, resume correctness, and regression rate. Rollback: disable the candidate workflow and return to the last-known-good graph path.
- [DynamicMCPBench](https://aclanthology.org/2026.emnlp-industry.202/) builds effect checkpoints from live MCP-server trajectories and scores observed state changes. Its published evaluation reports that longer tool chains sharply reduce success, even for the strongest evaluated agents. AUREN owner mapping: provider lifecycle adapters, evidence envelope, Arena, and issue #692 learning gate. Gate: use pinned server state, three independent attempts, effect-level assertions, and a separate held-out evaluator; retain failures and unknown outcomes. Rollback: reject promotion and reconcile any uncertain external side effect before retry.
- [Akamai Agent Sandbox](https://www.akamai.com/products/akamai-agent-sandbox) is a current comparison point for nested WebAssembly execution, resource limits, and deny-by-default outbound access. Reuse the concepts only through AUREN's existing execution/provider owners. Host-enforced containment and independent audit remain deployment gates; repository tests cannot certify equivalence.

These are research inputs and benchmark requirements, not proof of equivalent AUREN capabilities. Keep adapters opt-in and require paired measured value, independent evaluation, and a tested rollback before promotion.
