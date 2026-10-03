# AUREN native core

This crate contains deterministic kernels that are candidates for native
execution: hashing and empirical resource routing.

The Python runtime remains the canonical orchestrator. Native execution is
optional and fail-open to the existing Python implementation; a native failure
must never bypass policy, evidence, verification, or review gates.

Migration rule:

- Rust owns deterministic computation and bounded data transforms.
- Python owns orchestration, provider/model integration, policy, evidence
  lifecycle, UI/service adapters, and compatibility surfaces.
- New Rust functionality must have a stable JSON-lines boundary before an
  in-process binding is introduced.
- A Rust result is an optimization, never independent authority.

Build and test:

    cargo test --manifest-path rust/auren-core/Cargo.toml

Run the JSON-lines worker:

    cargo run --manifest-path rust/auren-core/Cargo.toml --bin auren-core

This separation lets the native core be adopted incrementally by packaged
installations without forcing Rust onto existing Python users.
