# External Environment Interaction Contract

This protocol creates a stable boundary for testing HWS in environments it does
not control. The environment remains the source of observations, tools, state,
and execution authority.

The contract covers:
- text, image, audio, video, and structured observations
- bounded action types
- explicit step horizons
- hidden state
- unfamiliar external tools
- content-addressed episode traces

The evaluator only scores externally supplied traces. It does not fabricate
observations, execute tools, or promote capabilities.

## Why this matters

A general system needs evidence beyond static engineering tasks. External
environment traces allow evaluation of multimodal perception, tool use,
long-horizon planning, adaptation to hidden state, and action consequences
without moving those environments into the adaptive runtime repository.

Repository tests validate boundary behavior only. A real evaluation must use
fresh external environments and independently attested outcomes.
