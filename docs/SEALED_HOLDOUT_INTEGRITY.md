# Sealed holdout integrity and receipt binding

Every sealed campaign request has a canonical `request_digest` computed from:

- campaign and corpus identities;
- unique run nonce;
- the exact ordered case envelopes, including case IDs, task/input/environment digests, holdout assignment, and transfer dimensions.

The exchange format is version 3 and carries that digest plus signed per-case evaluation metrics. Import recomputes the request digest after validating the envelope digest, so an attacker cannot mutate a case or holdout assignment and merely recompute the outer transport digest.

An external outcome receipt must carry the same request digest. `SealedArenaBoundary` rejects receipts whose request digest differs, even if the campaign ID, corpus ID, case IDs, and nonce are otherwise unchanged. This closes the gap where a receipt could be reused against a changed case manifest that retained the same caller-supplied corpus string.

The persistent SQLite replay ledger enforces a unique nonce across process restarts and concurrent claims. The evaluator gateway also validates immutable argument/environment contracts, finite positive timeouts, positive integer output budgets, and rejects unsafe environment overrides. Output size is bounded while streaming, rather than after full capture.

## Trust limitation

The digest proves integrity relative to the supplied request; it does not prove the corpus owner is independent or that a claimed holdout was genuinely withheld during development. The trusted corpus owner/evaluator must compute and retain the corpus manifest digest and sign the exact request/receipt lineage. Independent campaigns still require a separately controlled evaluator and OS-level isolation.
