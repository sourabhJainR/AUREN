# P1 Implementation

P1 adds reusable intelligence around the P0 state/evidence contracts while keeping the core dependency-free.

## Repository DNA

`.ai-harness/runtime/p1.py` provides a normalized repository profile and targeted invalidation. Profile facts retain status and evidence IDs. Changed paths invalidate only affected domains.

## Regression Genome

Regression inputs from accepted bug fixes, review findings, user corrections, and incidents are stored as candidate cases only. Candidate creation rejects credential-like content and never makes a case mandatory. Promotion requires two distinct deterministic passing runs, a sealed holdout result from a dataset different from training, independent evaluator identity, explicit independent human approval, and a trusted evidence verifier for every referenced result. Missing or invalid evidence leaves the case in candidate state. The lifecycle records candidates but does not auto-promote them.


Regression cases are deterministic records with stable IDs derived from trigger and expected outcome. They can be promoted from reviewed failures or user corrections; promotion policy remains outside the runtime so no accidental self-modification occurs.

## Engineering Memory and Proof Graph

The runtime provides evidence-bearing graph nodes and edges. It intentionally does not require Neo4j, Graphify, or code-mem. Those systems can supply richer nodes/edges through the extension layer.

Recommended durable relationship:

`requirement -> evidence -> decision -> change -> verification -> review -> outcome`

## Extension negotiation

Extensions advertise capabilities. The core can select available capabilities and explicitly reports missing ones. Optional failures degrade to core behavior rather than blocking ordinary work.

## Safety boundary

P1 is an information and coordination layer. It does not execute arbitrary code, install providers, mutate repository policy, or silently promote learned behavior.

## Host integration

The same P1 artifacts can be consumed by Claude Code, Codex, Gemini CLI, OpenHands/ACP, CI, or another host through thin adapters. Host-specific commands remain outside the core runtime.
