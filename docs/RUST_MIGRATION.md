# Rust migration boundary

AER uses a staged Python-to-Rust migration.

## Eligible for Rust

The first native slice is deliberately limited to deterministic kernels:

- SHA-256 and other pure hashing/identity operations
- bounded resource-route scoring from historical observations
- future content-addressed repository indexing and other pure data transforms

These operations have stable inputs/outputs, little provider-specific behavior, and no authority over whether an engineering action is allowed.

## Remaining Python-owned areas

Keep these in Python until a stronger boundary is proven:

- orchestration and graph execution
- provider/model adapters and local-LLM lifecycle
- evidence, verification, review, repair and promotion policy
- service/OS lifecycle and compatibility surfaces
- interactive engineering console/API
- plugin and external-system integration

These components coordinate side effects or encode policy. Moving them first would make migration harder to validate and would risk creating two policy owners.

## Compatibility rule

The Python implementation is always available. The optional native worker is selected only when an aer-core executable is present and returns a valid response. A native failure falls back to Python and never bypasses a gate.

The JSON-lines boundary is intentional: it permits the next step to become an in-process PyO3 binding, a packaged native executable, or another host without changing the Python contract.

## Migration stages

1. Define and test the pure Rust kernel.
2. Add the JSON-lines compatibility boundary.
3. Keep Python fallback authoritative.
4. Measure native versus Python behavior and performance.
5. Promote only after equivalence, failure-injection and portability evidence.
6. Move additional deterministic kernels one at a time.

No Rust kernel is allowed to become a second orchestrator or policy authority.
