# Evaluator-Gated Partial Graph Dependencies

AUREN may continue a synthesis or aggregation node after an optional provider fails only when the graph explicitly declares that behavior.

## Contract

- Nodes default to strict dependency behavior. A failed, blocked or skipped dependency prevents the dependent node from running.
- A node may set `allow_partial_dependencies=True` only when it has an evaluator.
- Only `FAILED` dependencies whose source nodes are explicitly `critical=False` may be accepted as partial input.
- A failed critical dependency remains blocking.
- `BLOCKED` and `SKIPPED` dependencies always remain blocking. Partial mode is not a way around authority, budget, cancellation or prerequisite gates.
- The node receives the exact failed dependency identities in `auren_partial_dependencies`. It receives only the error type, never raw provider exception text.
- The result records accepted partial dependency identities in NodeResult, evidence, trajectory and replay. The canonical StateGraph trace binds those identities to the downstream node span and records the partial-node count on the supervisor run. Raw provider exception text is not exported.
- The policy is included in the graph digest so a strict and partial-capable graph cannot share an identity.

## Intended use

Use this for optional evidence collectors such as secondary news, sector context or social sentiment when required company data is still available. The synthesis evaluator must check the partial marker, required evidence, freshness and coverage before accepting the result. The recommendation layer must lower confidence/coverage or abstain when required evidence is missing.

Do not use this for required identity, policy, approval, verification, isolation, persistence or release dependencies. Those remain critical and fail-closed.

## Regression cases

The suite verifies: optional provider failure with evaluator-approved synthesis; critical provider failure remains blocking; skipped dependencies cannot be bypassed; missing evaluator rejects the graph; and graph digest/replay preserve the partial policy.
