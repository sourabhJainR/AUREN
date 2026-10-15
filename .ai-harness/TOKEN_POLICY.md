# Token and Tool Economy Policy

Context, model calls, tool calls, and command execution are all budgeted resources.

## Default strategy

Use the smallest context and least capable model that can safely solve the current phase.

Escalate budget when:

- uncertainty is unknown;
- risk is high or critical;
- verification fails;
- the task spans multiple architectural boundaries;
- independent review finds a material issue.

## Context priority

1. current task and acceptance criteria
2. repository instructions and architecture boundaries
3. relevant code and tests
4. relevant prior phase evidence
5. relevant learned patterns
6. general background

Drop low-value context before increasing the budget.

## Reuse

Cache or preserve stable repository context where the provider supports it. Reuse repository maps and phase summaries instead of regenerating equivalent content.

## Tool discipline

Load only tools required by the current phase. Avoid broad discovery after the relevant evidence is known.

## Completion economics

Do not reduce verification merely to save tokens. Optimize repeated context preparation and unnecessary exploration first.

Record token/tool metrics when the provider exposes them so future routing can learn which workflows are efficient and which are wasteful.


## Quota exhaustion and idle supervision

Reuse the durable SQLite task state and lease in `portable.execution_controller.ExecutionController`; do not add a parallel scheduler or task database. Provider adapters should raise `ProviderQuotaError` or preserve HTTP status and Retry-After metadata so the controller can classify the failure.

- A temporary rate limit is stored as `waiting_quota` with provider, bounded diagnostic, attempt count, and a next eligible timestamp.
- Permanent account/billing exhaustion is stored as `blocked_quota` and requires operator intervention; never poll a provider when no reset is known after the bounded retry budget.
- Resume only due work from the existing supervisor. The timer/service owner should sleep until the nearest local due time and wake on new work or shutdown; do not spin, poll provider endpoints, or invoke an LLM while idle.
- Revalidate task lease, handler implementation digest, repository revision, permissions, and required gates before consequential work resumes. Do not replay committed non-idempotent effects.
- Keep task contracts, acceptance criteria, decisions, evidence references, and the next action in the existing durable checkpoint envelope. Treat provider context as disposable.
- Protected compaction sections must fit intact. If they cannot fit, fail closed and split/re-scope the task or raise the budget; never truncate authority, acceptance, security, or evidence constraints.

## Proactive repository-native design advice

Before non-trivial implementation, reuse `ENGINEERING_PRINCIPLES.md`, `.ai-harness/ENGINEERING_DESIGN_POLICY.md`, `portable/engineering_design_guard.py`, the task planner, and repository context already gathered by the orchestrator. Inspect the target repo's manifests, language/framework versions, existing implementations, tests, and architecture boundaries. Offer a concise best-fit algorithm/workflow/pattern, viable alternatives, concrete trade-offs, compatibility risks, tests, and rollback plan. Tie recommendations to inspected repository evidence and the target stack's own idioms. Do not create another policy engine or pattern catalogue, introduce an unverified dependency, or force a pattern onto trivial work. Unknown language/framework versions must be explicitly called out rather than guessed.
