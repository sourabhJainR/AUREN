# Compiler Bootstrap Learning Priors

This document records reusable engineering priors learned from reviewing and testing a staged self-hosting compiler effort.

These are advisory priors, not evidence. They do not grant execution authority, change security policy, or qualify a capability for promotion. AUREN must still observe fresh runs, verification, independent evaluation, regression replay, and holdout performance before trusting any strategy.

## Initial priors

1. Separate pipeline replay from self-hosting. Running source components through a native interpreter is useful stage evidence, but self-hosting requires the compiler produced by an earlier stage to compile the compiler source itself.
2. Make every bootstrap boundary explicit. Track stage input digest, compiler identity, output artifact digest, toolchain identity, target, and environment.
3. Use differential checks before fixed-point checks. Compare each self-hosted component against an independently implemented reference before requiring stage-to-stage byte identity.
4. Treat termination as a compiler invariant. Source-level loops, branch lowering, parser scanners, and token consumers need explicit progress and termination checks.
5. Distinguish value-producing and statement-producing control flow. Branch lowering must preserve expression values without manufacturing stack values for terminating branches.
6. Prefer structured intermediate representations over serialized strings. Production stages should preserve token spans, AST identity, types, effects, ownership state, and source mappings.
7. Make errors first-class across every stage. Lexer, parser, semantic analysis, lowering, code generation, and runtime execution need stable error categories and source locations.
8. Test negative paths as aggressively as happy paths. Malformed indentation, unterminated strings, invalid operators, unknown identifiers, ownership conflicts, cyclic modules, and invalid IR should have deterministic diagnostics.
9. Make bootstrap reproducibility content-addressed. Re-running the same stage should produce the same canonical artifact, and the next stage should consume the prior stage artifact.
10. Keep the seed small and auditable. A bootstrap chain should add only the minimum language and compiler capability needed at each stage.
11. Separate compiler correctness from compiler intelligence. A self-hosting compiler demonstrates compiler completeness and bootstrap integrity; intelligence claims require independent tasks, transfer, holdouts, and outcome-based learning.
12. Use failure observations as learning material, not automatic truth. A failed build or test should become a structured observation with failure class, reproduction, fix, and verification evidence.
13. Preserve a repair chain. Retain failure, diagnosis, change, verification, regression replay, and later outcome as one learning unit.
14. Avoid cross-project coupling when transferring learning. Reusable lessons should enter AUREN as abstract patterns or priors without source-project code, paths, dependencies, or execution authority.
15. Promote only after independent evidence. A bootstrap strategy should move from prior to candidate to replay to shadow to holdout/canary to trusted policy, with rollback retained.

## Recommended AUREN learning fields

- stage name and stage contract;
- input/source digest;
- compiler identity;
- target/toolchain identity;
- artifact digest;
- diagnostic class and source span;
- failure reproduction;
- repair change identity;
- verification result;
- differential result;
- regression result;
- holdout result;
- resource and timing measurements;
- confidence and sample count.

The key lesson is simple: make the compiler prove each boundary before allowing the next boundary to inherit trust.