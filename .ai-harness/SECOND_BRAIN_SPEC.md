# AUREN Second Brain

AUREN can act as an engineering-focused second brain around the coding agent. It is not a general-purpose personal assistant by default. Its purpose is to preserve durable context, decisions, preferences, and evidence so an agent can help across sessions without replaying transcripts.

## Model

```text
                    User / Team Intent
                           |
             +-------------+-------------+
             |                           |
        Identity                       Profile
      stable rules                 current priorities
             |                           |
             +-------------+-------------+
                           |
                    Durable Memory
             facts / decisions / lessons
                           |
                  Knowledge Fabric
          repo / graph / docs / task evidence
                           |
                    AUREN Agent Loop
                           |
          +----------------+----------------+
          |                |                |
       Execute          Verify           Review
          |                |                |
          +----------------+----------------+
                           |
                        Outcome
                           |
                    Evidence-backed
                        Learning
                           |
                     Memory update
```

The durable engineering state remains:

`INTENT -> CONTRACT -> REPO_FACTS -> DECISIONS -> EVIDENCE -> CHANGESET -> VERIFY -> OUTCOME -> OPEN_RISKS -> NEXT`

Second-brain memory supplements this state; it does not replace it.

## Three core memory layers

### Identity

Stable operating rules for the agent. Examples: repository safety rules, communication style, protected behaviors, approval boundaries, and non-negotiable engineering principles.

Identity is human-authored and must not be rewritten by learning.

### Profile

Current working context: active projects, priorities, recurring responsibilities, preferred tools, and known constraints. Profile can change, but changes should be explicit or supported by evidence.

### Memory

Durable facts, decisions, lessons, preferences, and outcomes learned from completed work. Each promoted item should have provenance and evidence. A failed or one-off observation must not become permanent policy.

## Heartbeat

An optional heartbeat periodically wakes the second-brain coordinator. The default implementation is safe and read-only: it reads local task/evidence sources and produces a bounded list of suggested actions.

A heartbeat must:

- be explicitly enabled by the user;
- have a configurable interval;
- be idempotent;
- use bounded context;
- report why an action was suggested;
- never silently send messages, merge code, change permissions, or install integrations;
- distinguish suggestion, prepared action, and executed action;
- stop on repeated non-progressing runs.

External sources such as email, Slack, Jira, calendars, GitHub, or task managers are optional adapters. AUREN must not require them for core operation.

## Proactive work

The same pattern applies beyond coding. AUREN can prepare structured drafts or actions for recurring digital work such as:

- engineering status updates;
- design or review summaries;
- issue triage;
- release notes;
- proposals and statements of work;
- customer-facing technical responses;
- documentation updates;
- meeting preparation and follow-up.

The system treats these as artifacts with contracts and verification, not as unrestricted autonomous activity.

## Security and privacy

Never store credentials, access tokens, recovery secrets, private keys, or raw personal inbox content in repository memory. Prefer references, summaries, hashes, and bounded excerpts.

Personal or organization-specific memory belongs in a user-controlled local location and is ignored by Git by default. Repository examples must use synthetic data.

Learned memory can improve retrieval and suggestions but cannot modify security policy, permissions, approval requirements, or immutable identity rules.

## Context policy

At session start, load only the small identity/profile layer. Retrieve memory selectively by task. Include provenance and confidence. Prefer recent, relevant, high-confidence facts over a large memory dump.

A compact handoff should contain:

`known -> proven -> inferred -> decisions -> open risks -> next action`

This keeps the second brain useful without turning it into a transcript archive.
