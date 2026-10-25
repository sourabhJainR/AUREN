# ArmorIQ Capability Benchmark for AUREN Guard

**Review date:** 2026-10-25  
**Purpose:** use public ArmorIQ product and documentation descriptions as a security-capability benchmark. This is not a claim that either product has been independently validated.

## Public ArmorIQ offering reviewed

Official references:
- [ArmorIQ Intent Engine](https://armoriq.ai/products/intent-engine)
- [ArmorIQ Intent Container](https://armoriq.ai/products/intent-container)
- [ArmorIQ Platform documentation](https://docs.armoriq.ai/platform)
- [Policy management](https://docs.armoriq.ai/sdk/concepts/policy-management)
- [Enforcement outcomes](https://docs.armoriq.ai/tools/enforcement)
- [Security model](https://docs.armoriq.ai/sdk/concepts/security-model)

The published offering describes pre-execution intent capture and policy evaluation, signed plans and step verification, identity/scope binding, runtime allow/hold/deny decisions, tamper-evident audit evidence, agent and MCP registries, policy authoring/analytics, and (for Intent Container) system-level enforcement across files, processes, network calls, and IPC. Public policy examples also describe time windows, rate limits, IP allowlists, tool-level rules, and hierarchical policy scope. Product availability and deployment requirements should be verified with the vendor; published claims are not independent assurance.

## AUREN parity assessment

| Capability | AUREN baseline/evidence in repository | Status / required work |
|---|---|---|
| Pre-dispatch authorization | Durable ExecutionController preflight; registered handler execution classes; approval and promotion gates | **Partial.** Ensure every provider/tool dispatch path is mediated, including nested tools and subprocesses. |
| Organization-configurable policy | New optional `.auren/guard_policy.json`, versioned schema, deny-overrides, default-deny, approval requirement, policy digest | **Initial implementation.** Add inheritance, explicit policy priority, partial wildcards, time windows, rate limits, resource/network scopes, and validation tooling. |
| Fail-closed policy evaluation | Invalid/oversized/symlinked/duplicate-key policy blocks task preflight | **Partial.** Add concurrent policy update tests and independent adapter-boundary enforcement. |
| Intent-to-action binding | Trusted registered `guard_action`; current Guard decision rechecked before consequential provider dispatch; action, policy digest, effect, and matched rules bound into the durable side-effect ledger and provider operation payload | **Partial.** This binds registered actions and current policy to the consequential dispatch path. Still need a canonical immutable plan commitment and enforcement for every nested tool/MCP call. |
| Cryptographic plan integrity | Existing AUREN evidence and signing components in other paths | **Not parity-verified.** Build and verify per-action proof/chain from approved plan through provider dispatch and result. |
| Delegation and scope narrowing | Existing supervisor/approval architecture | **Gap.** Carry parent intent, delegated subset, expiry, and revocation lineage across child agents; reject scope widening. |
| Runtime tool interception | Existing provider lifecycle and controller paths | **Partial.** Inventory all execution routes; enforce in the common execution boundary and fail closed on unmediated tool adapters. |
| System-level containment | Existing process-isolation and bounded-subprocess components | **Not equivalent to kernel-level objective containment.** Validate filesystem, process, network, IPC, and child-process isolation independently; state unsupported platforms explicitly. |
| Agent/tool inventory and scanning | Repository intelligence and engineering dashboard | **Gap.** Build a trust inventory for agents, adapters, MCP servers, owners, permissions, versions, scan results, and revocation status. |
| Audit evidence | Durable engineering events, evidence capture, side-effect ledger; workflow-profile receipt metadata | **Partial.** Bind each policy decision to exact action ID, policy version/digest, intent digest, decision, approver, provider operation ID, and verified outcome. Detect missing links explicitly. |
| Organization console and policy lifecycle | CLI workflow profile; organization Guard JSON policy | **Gap.** Add inspect/validate/dry-run/activate/rollback commands and dashboard projections before introducing a multi-tenant UI. |
| Compliance mappings | Existing evidence and governance foundations | **Not demonstrated by this change.** Map controls to selected standards only when evidence supports the claim; do not advertise certification based on logs alone. |

## Security architecture requirements

1. **One policy decision point, enforced at the action boundary.** A plan-time check is not enough; revalidate every tool/provider operation immediately before dispatch.
2. **Separate policy from authority.** Organization policy can further restrict. It cannot grant credentials, bypass approvals, ignore branch protection, or override mandatory platform rules.
3. **Explicit precedence.** Platform invariants → organization policy → project policy → agent/tool policy → task intent. Deny overrides allow for mandatory safety rules; any alternative priority model must be explicit, versioned, tested, and unable to override platform invariants.
4. **No spoofable context.** Derive organization, agent, execution class, credential identity, and verified resource identity from trusted supervisor/provider context. Treat user payload fields as untrusted selectors, never as authoritative identity or risk labels.
5. **Policy change and revocation.** Version and digest policies, atomically activate them, log approvals, and re-evaluate on every dispatch. A changed policy must constrain subsequent actions; unknown remote outcomes still require reconciliation.
6. **Cryptographic evidence.** Bind intent, policy, action, and result with canonical digests and signed or append-only evidence. A hash alone detects some edits but does not prove who authorized the action.
7. **Defense in depth.** Guard must not claim filesystem/network/kernel containment unless the corresponding OS-level enforcement is active and independently tested.
8. **Adversarial acceptance tests.** Cover prompt injection, action substitution, path traversal, symlink and TOCTOU races, delegation escalation, policy tampering, stale policy, replay, parallel dispatch, restart, cancellation, unknown provider outcomes, and evidence export failure.

## Delivery sequence

- **Phase 1 — current PR:** organization policy schema/evaluator, fail-closed supervisor integration, workflow-profile receipts, CLI profile lifecycle, deterministic tests, and impact-policy classification.
- **Phase 2 — action-bound dispatch (follow-on PR):** re-evaluate Guard policy at consequential provider dispatch and bind the registered action and policy digest to durable side-effect/provider receipts. This is not yet universal nested-tool mediation.
- **Phase 3 — immutable action-plan binding:** immutable intent/action graph and per-dispatch verification across every provider/tool adapter.
- **Phase 4 — organization policy lifecycle:** inheritance, priority, time/budget/rate constraints, dry-run, policy diff, activation, rollback, and audit views.
- **Phase 5 — isolation and inventory:** adapter/tool inventory, least-privilege profiles, process/filesystem/network boundaries, and supported-platform fault injection.
- **Phase 6 — evidence and assurance:** cross-process restart and revocation tests, cryptographic receipt lineage, independent evaluation, and narrowly worded compliance evidence.

**Release gate:** do not claim full ArmorIQ parity until the action-boundary inventory is complete, every dispatch path is proven mediated, policy changes/revocation are restart-safe, and the relevant security acceptance suites pass. Kernel-level isolation and external certifications require separate implementation or external assurance.
