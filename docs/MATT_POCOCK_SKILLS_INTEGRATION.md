# Matt Pocock Skills Integration

AUREN reviewed the current `mattpocock/skills` engineering set, including the retrospective skill and the surrounding engineering flows.

## Adopted as AUREN-native skills

- `retro`: evidence-backed post-session improvement discovery.
- `research`: bounded primary-source research with explicit unknowns and provenance handoff.
- `prototype`: disposable, measurable experiments that must promote through normal gates.
- `resolving-merge-conflicts`: intent-preserving conflict resolution using repository impact evidence.

These are thin orchestration surfaces. They reuse AUREN's canonical repository intelligence, context/evidence envelope, provenance ledger, verification and rollout contracts.

## Already represented in AUREN

The current system already contains aligned forms of the important engineering disciplines for:

- routing;
- requirements grilling;
- specification and ticket decomposition;
- implementation and TDD;
- bug diagnosis;
- domain modeling;
- codebase/module design;
- code review;
- architecture improvement;
- phase boundaries, handoffs and durable execution.

Those capabilities were not duplicated.

## Not copied directly

The following ideas were intentionally not imported as independent runtimes or stores:

- alternate memory or context stores;
- another repository graph/index;
- another planner or task state machine;
- another provenance/receipt ledger;
- agent-specific tooling that duplicates existing AUREN capabilities;
- process prose that does not change runtime behavior.

Where a source skill overlaps an existing AUREN construct, the AUREN construct remains authoritative and the skill is only a routing/documentation layer.

## Version

This integration increments the AUREN plugin from `21.0.0` to `22.0.0` because it adds new composable engineering capabilities and corresponding contract tests.
