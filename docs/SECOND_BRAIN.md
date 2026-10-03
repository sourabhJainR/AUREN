# Second Brain for AUREN

AUREN now includes an optional second-brain capability inspired by the idea of a local system that learns how a person or team works over time and helps prepare work proactively.

## What it adds

1. **Identity**: stable human-authored rules and approval boundaries.
2. **Profile**: current priorities, responsibilities, preferences, and constraints.
3. **Memory**: bounded, provenance-backed facts, decisions, lessons, preferences, and outcomes.
4. **Heartbeat**: an opt-in periodic read-only check that turns local state into explainable suggestions.
5. **Proactive artifacts**: support for preparing engineering updates, issue triage, review summaries, release notes, customer responses, proposals, meeting preparation, and similar digital work.

This extends the existing AUREN control plane rather than creating a separate autonomous agent.

## Why this belongs in AUREN

The useful pattern is not “put everything about a person into one giant prompt.” It is:

```text
small stable context
       +
selective durable memory
       +
repository / business evidence
       +
bounded workflow
       +
verification
       +
outcome feedback
```

The agent can therefore remember decisions and working preferences across sessions while still retrieving only what matters for the current task.

## Safe local layout

Use the checked-in templates under `.ai-harness/second-brain/` as a starting point. User-specific material should live outside source control, for example:

```text
.ai-harness/local-second-brain/
  IDENTITY.md
  PROFILE.md
  MEMORY.jsonl
```

The local directory should be ignored by Git. Never place passwords, API tokens, private keys, recovery codes, or raw confidential inbox content in memory.

## Heartbeat contract

Run the safe heartbeat with:

```bash
python scripts/heartbeat.py
```

It reads local task/outcome JSON when present and prints suggestions. It performs no external action. An integration adapter may later turn a suggestion into a prepared or executed action only when that adapter has explicit permission and its own safety contract.

A scheduler can invoke the command periodically, but scheduling is deliberately not enabled by the repository itself.

## Memory promotion

Memory is not policy. A durable lesson should have:

- a source;
- evidence IDs;
- confidence;
- task scope when applicable;
- an intent link when the observation is task-specific.

Repeated successful observations can inform future retrieval and recommendations. They cannot rewrite immutable guardrails, security policy, or permissions.

## Example lifecycle

```text
Conversation / task
        |
        v
  useful decision
        |
   verification
        |
      outcome
        |
 evidence-backed lesson
        |
 bounded future retrieval
        |
 better next run
```

The result is a practical engineering second brain: persistent enough to learn, bounded enough to stay useful, and controlled enough not to become an unrestricted autonomous system.
