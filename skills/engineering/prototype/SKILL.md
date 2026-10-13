---
name: prototype
description: Build a small disposable experiment to answer a concrete engineering question without silently becoming production architecture.
disable-model-invocation: true
---

# AUREN Prototype

Use this skill when uncertainty is best resolved by a runnable experiment rather than more discussion or production implementation.

## Contract

A prototype has one explicit question, a bounded scope, a measurable success signal and a disposal or promotion decision.

## Flow

`question -> hypothesis -> minimal slice -> measure -> compare -> decide -> record`

1. Read the repository map and relevant contracts first.
2. Define the hypothesis and the smallest experiment that can falsify it.
3. Reuse canonical context, interfaces and test fixtures where practical.
4. Keep prototype code isolated from production ownership unless promotion is explicit.
5. Measure the agreed signal and capture inputs, output and limitations.
6. Decide: discard, iterate once with a changed hypothesis, or promote through the normal implementation path.

## Guardrails

- No new persistent repository graph, memory store or evidence store.
- No hidden production dependency from a prototype.
- No weakening of security, permission or verification gates.
- No benchmark claims without reproducible inputs and a receipt.
- Promotion requires normal implementation, tests, review and rollout gates.

## Output

Record the question, hypothesis, experiment boundary, result, evidence identifiers, unknowns and promotion/disposal decision.
