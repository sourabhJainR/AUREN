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

## Repository artifact freshness implementation slice

The optional extension inventory now checks graph/index artifacts against a sidecar manifest named `<artifact-name>.auren-manifest.json`. The manifest binds the artifact SHA-256 to a repository snapshot SHA-256. Snapshot computation is bounded to 20,000 paths and 64 MiB of file content; an incomplete scan, missing manifest, malformed digest, changed artifact, changed repository, or path outside the repository is never marked fresh. The capability planner only exposes an extension hint when trust, freshness, availability, and approval policy all pass.

The manifest builder returns data for the artifact producer to persist; it does not mutate or install extensions. Existing external graph tools do not automatically produce this manifest, so their artifacts remain unverified until an adapter writes the manifest after generation. The repository context page cache now supports replace-on-refresh: unchanged content-addressed pages are reused, stale pages for the repository domain are evicted, and unrelated memory/history domains are preserved. This is not a substitute for per-file graph-neighborhood invalidation. Live task benchmarks remain open.

## Follow-on implementation order

1. **P0 — Prove metadata enforcement:** tests that output bounds, approval, provider scope, freshness, and failure behavior are enforced at the actual invocation boundary, not merely displayed by the inventory.
2. **P0 — Recovery and state:** continue exact-head crash/restart/reconciliation tests for checkpoint, journal, provider outcome, cancellation, and shared-budget boundaries. Unknown remote outcomes must remain blocked pending reconciliation.
3. **P1 — Context economics:** benchmark raw versus bounded tool output on representative tasks. Record input/output tokens where the host exposes them, latency, retained evidence, missed errors, and task success. Keep raw artifacts accessible for audit.
4. **P1 — Repository map freshness:** bind cached structural results to content digests and invalidate affected graph neighborhoods after edits. Preserve parse errors and omitted-file warnings.
5. **P1 — Regression genome:** turn accepted bug fixes, review findings, and user corrections into candidate tests; promote only after deterministic regression and independent holdout evaluation.
6. **P2 — Domain skill packs:** add UI, product, video, or career workflows only as optional, task-triggered packs with manifest, license, permissions, output limits, and tests.

## Verification requirements

- Run python .ai-harness/extension_registry.py and python -m unittest tests.test_extension_registry -v.
- Run the repository's applicable architecture, hardening, portable-bundle, runtime-resilience, and integration-conflict checks on the exact PR head.
- Confirm the branch's final head before merge; after merge, verify required checks on the resulting main commit.
- Roll back this change by reverting the extension inventory/manifest/test commits. No external tool configuration or installation is changed by this change.

## Limits

This is a design and repository implementation review, not an independent security audit of the listed projects. Availability detection is not a health check, a manifest is not proof of trust, and local parsing does not by itself prove sandbox isolation. External benchmark claims are not adopted as AUREN performance claims.
