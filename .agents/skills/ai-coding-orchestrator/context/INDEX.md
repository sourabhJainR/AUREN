# AUREN Orchestrator Context Index

This directory is a **progressive-discovery context pack**. `SKILL.md` is the only always-loaded contract. Do not preload these packs.

| Pack | Load when |
|---|---|
| `lifecycle.md` | routing, planning, execution, bounded loops, graph orchestration |
| `verification.md` | tests, regression, review, recovery, release gates |
| `frameworks.md` | a problem-solving decision point requires OODA/DMAIC/RCA/etc. |
| `providers.md` | provider projection, Claude/Codex/Gemini/ChatGPT integration |
| `learning.md` | learning, self-improvement, candidate orchestration changes |
| `benchmarking.md` | Behavioral Conformance Suite, objective oracles, benchmark design |

## Retrieval contract

1. Start with the task contract and current repository state.
2. Retrieve at most the smallest pack(s) needed for the current phase.
3. Prefer exact sections/files over whole packs when the runtime supports targeted reads.
4. After the decision, retain only digests, decisions, constraints and evidence.
5. Re-discover a pack when new evidence changes the decision.

## Hard budget

The always-loaded skill remains under the configured 9,000-character skill budget. Context packs are not counted as active skill context until explicitly retrieved.

## Safety

Context packs never override repository rules, security/permissions, acceptance criteria, protected behavior or verification gates.
