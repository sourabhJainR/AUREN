# Context Engineering Policy

AUREN uses **progressive disclosure at runtime**, not context bolting.

## Context Broker contract

`DISCOVER -> SCORE -> LEASE -> USE -> COMPRESS -> RELEASE`.

The Context Broker is the authority for prompt-context selection. Context exists as a candidate until a current phase, question, gate, uncertainty or dependency justifies it. A candidate carries provenance, reason, relevance, confidence, freshness, risk and estimated cost.

1. Start with the minimum immutable task contract and repository safety/instruction boundary.
2. Ask the broker what evidence is required for the current decision.
3. Score candidates for task/phase relevance, confidence, freshness, risk and context cost.
4. Materialize only selected candidates immediately before use and enforce a hard budget.
5. After the decision, retain only references, digests, decisions, constraints and proof-bearing evidence; release raw context.
6. Re-discover source when later evidence makes it necessary. Never assume earlier context remains active.

Required context may block when it cannot fit the budget; optional context must yield before the budget grows. Security, acceptance and protected-behavior evidence cannot be displaced by convenience.

## Context layers

- **Always active:** task intent, boundaries, acceptance, security/permissions and current state.
- **On demand:** repository structure, exact files/symbols, tests, architecture, domain knowledge, history, memory, framework, specialist capability and external research.
- **Transient:** raw documents, logs, tool output and phase-specific source text. These are leased only for the decision that needs them.
- **Durable:** compact ledger entries, evidence digests, decisions, outcomes, regression signals and provenance.

Do not replay entire transcripts or repository dumps unless recovery explicitly requires them.

Stable context should remain cache-friendly. Content-addressed pages may be reused, but cache presence does not make content active; the broker must select it again for the current decision.

When context exceeds budget, reduce low-value material before increasing the budget. Optimize verified outcome per token, call, retrieval, retry and latency.
