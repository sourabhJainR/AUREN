# Unified Agent Capability Contract

This is the AUREN mapping of useful Hermes Agent patterns. It is not a copy of the Hermes application. AUREN remains authoritative for repository rules, security, sandboxing, acceptance, verification, evidence, learning and promotion.

## Runtime path

```text
INTENT
 -> CONTEXT
 -> CAPABILITY PLAN
 -> TASK PLAN
 -> PROVIDER / TOOL ROUTING
 -> PARALLEL READ-ONLY WORK
 -> SERIALIZED MUTATION
 -> OBSERVE / RECOVER
 -> VERIFY
 -> QUALITY GATE
 -> REVIEW
 -> LEARN AS CANDIDATE
```

## Capability families

- Provider adapters: capability-based, deterministic selection with explicit preference and priority.
- Memory: SQLite WAL, FTS5 recall, project and intent scoping, redaction and approval-aware writes.
- Skills: metadata-first discovery and prerequisite checks before loading procedures.
- Delegation: bounded worker pool; parent AUREN task remains responsible for acceptance and verification.
- Scheduling: durable claim-before-run state, bounded retry count and no privileged scheduler path.
- Background work: receipts and lifecycle are represented as durable task state; execution remains under AUREN controls.
- External tools: MCP, browser, web/X search, vision, image generation and TTS are adapter capabilities and never assumed available.
- Terminal/code execution: sandbox is mandatory for risky local execution.
- Output quality: completion requires acceptance, verification, evidence, clean diff, clean scope and no unresolved findings.

## Safety invariants

1. Provider fallback changes transport, not acceptance or security policy.
2. Memory is evidence, not authority.
3. Skills are knowledge, not authority.
4. Scheduled work re-enters the same AUREN lifecycle.
5. Delegated results are receipts, not proof of correctness.
6. External adapters must be discovered/configured before use.
7. Missing evidence blocks a pristine-success claim.
8. Learning can propose changes but cannot grant permissions or bypass replay, shadow, canary or rollback gates.

## Source boundary

The design was informed by the public NousResearch/hermes-agent capability model, including persistent memory, skills, delegation, scheduling, provider/model flexibility and isolated execution. The AUREN implementation is independently structured and keeps a single control plane.
