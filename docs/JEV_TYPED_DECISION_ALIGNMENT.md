# Jev-aligned typed decision integration

The project adopts the useful part of the Jev/TypeSafe design notes without adding a Jev runtime or making the system dependent on a provider.

## What is already native to AUREN

- **Choice / Noul / Score** are first-class typed decision primitives.
- **Probabilities and confidence** remain attached to the decision and are consumed by deterministic policy.
- **One state, many questions** is supported by `DecisionFabric` so independent judgments can be batched without creating an orchestration round-trip per question.
- **Code owns policy**: calculations, exact lookups, execution, permissions and safety gates remain deterministic code.
- **Evidence and verification remain separate** from model judgment. A plausible typed answer does not become truth by itself.
- **StateGraph** handles changing state, parallel work, joins, contracts and bounded convergence.
- **Regression and SkillOpt** already provide the learning and validation loop.

These are the parts worth keeping because they reinforce existing AUREN boundaries rather than replacing them.

## Small addition

`DecisionRecipe` is a thin declaration around the existing `DecisionFabric`. It keeps, in one reviewable object:

1. the intended behavior,
2. the typed questions,
3. optional thresholds,
4. explicit no-match choices where a choice may abstain,
5. a source/contract reference.

`AdaptiveInferencePolicy` now uses `INFERENCE_DEPTH_RECIPE` as its concrete example. The recipe does not execute anything, call a provider, or create another state machine. `DecisionFabric` remains the only evaluator.

## What we deliberately did not add

- no Jev SDK or service dependency;
- no generic LLM-to-JSON parser;
- no new agent runtime;
- no mandatory confidence threshold on every decision;
- no automatic replacement of deterministic code with model calls;
- no separate evidence store;
- no parallel orchestration layer just for decisions.

## Operating rule

Use a typed decision when the task needs bounded semantic judgment. Use ordinary code for deterministic rules, arithmetic, exact lookup, execution and policy. Keep an explicit no-match/abstain option when the domain permits "none of the above". Verify the resulting action independently before treating it as an engineering fact.

This keeps the useful Jev pattern while preserving AUREN's provider-neutral, evidence-first architecture.
