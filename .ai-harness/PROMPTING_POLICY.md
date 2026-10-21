# Prompting Policy

The harness uses a complete-job prompting pattern for provider calls.

## Rules

1. **Give the whole job upfront.** The effective prompt contains task, intent, repository context, constraints, boundaries, acceptance/exit criteria, and the expected response contract before the provider starts.
2. **Interview only for material ambiguity.** If missing information can change the implementation and cannot be established safely from repository evidence, emit `CLARIFICATION_NEEDED` before mutating work. Do not ask questions that repository inspection can answer.
3. **Explain why.** Decisions should preserve the reason behind constraints, not depend on a pile of arbitrary prohibitions.
4. **Define done.** Stop when the task contract, acceptance criteria, and required evidence are satisfied. Interesting adjacent findings remain deferred.
5. **Keep output focused.** Avoid chain-of-thought narration, repeated task statements, and verbose progress reports.
6. **Do not duplicate verification instructions.** The model should use harness evidence and repair failures; deterministic tests, diff checks, security gates, and acceptance evidence remain owned by the control plane.
7. **Preserve auditability.** The canonical prompt remains unchanged. The provider receives a derived effective prompt so the authored request and model-facing instructions can be distinguished.

## Design intent

This policy reduces iteration and token waste while improving autonomous decision quality. It does not weaken the harness's independent verification, review, scope, approval, or staged-change controls.


## Controlled communication and serialization

8. **Use controlled technical English.** Prefer short sentences, active voice, consistent terms, and one main instruction per sentence. Define unfamiliar acronyms. State blockers and next safe actions directly.
9. **Make status evidence-based.** Name the revision and evidence for material claims. Distinguish passed, failed, skipped, pending, unknown, and verified. A skipped check is not a pass.
10. **Prefer TOON for supported model-facing data.** Use TOON when the receiver supports it and the schema fits. Keep JSON for APIs, configuration, CLI output, or any contract that requires JSON. Do not change persisted or public formats without compatibility tests and rollback.
11. **Keep safety controls format-independent.** Apply redaction, authority checks, validation, and provenance before serialization. A compact format does not make data safe or trusted.

The detailed rules and communication templates are in [Communication and Structured-Data Policy](../docs/COMMUNICATION_AND_STRUCTURED_DATA_POLICY.md). This uses ASD-STE100 principles as guidance; it does not claim formal certification.
