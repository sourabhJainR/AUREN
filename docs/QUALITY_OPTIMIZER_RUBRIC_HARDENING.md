# Quality optimizer and rubric hardening

The optimizer and rubric contracts are additive trust-boundary helpers. They do
not replace the canonical provider lifecycle, evidence ledger, evaluator, or
Guard.

## Rubric binding and evidence

- Pin the rubric to the canonical plan manifest and verify the binding before
  execution and again at the trusted evaluation boundary.
- Resolve evidence by canonical evidence ID. Verify run, source, artifact,
  evidence type, verified status and custodian identity. A URL or caller-
  supplied string is not evidence.
- Keep executor, evaluator and evidence custodian identities distinct.
  Normalized string comparison rejects simple aliases but does not prove
  independent identity issuance or deployment isolation.
- Missing evidence is unscored. Unknown hard gates do not pass. Rubric results
  cannot authorize dispatch, merge, deployment or promotion.

## Measured optimizer receipts

`ProviderUsageReceipt` binds provider-reported token counts, latency, retries,
tool calls, request ID and provider/model version to task, context, policy and
rubric digests. Monetary cost remains unknown unless both cost fields and a
price revision are supplied. Estimates must never be represented as measured
provider usage.

`verify_paired_run_identity` requires task, source, context, tool, provider,
model tier, rubric, policy, privacy scope, acceptance and context-budget
identity to match before baseline/candidate comparison.

## Scoped context cache

`ContextPageCache` supports scoped reads and writes bound to tenant namespace,
privacy scope and context/model/tool/policy/repository/rubric revisions.
`ContextBroker` now accepts an injected scoped cache only when a scope
resolver is also supplied. It resolves scope for every candidate read and
records cache hit/miss telemetry. The resolver must derive scope from current
trusted runtime identity and policy state; callers must not accept tenant or
privacy values from task text.

A standalone local run may use a dedicated `local-anonymous` namespace and
`local-only` privacy scope. That namespace must never be shared with remote
tenants. The current cache remains process-local; persistent storage and
cross-host isolation are not implemented. A changed revision creates a new
cache identity. Explicit invalidation is available for the exact old scope.

## Provider dispatch validation

The general `HybridModelRouter` supports optional pre-dispatch availability
validation. The local composition now supplies a validator for its admission-
bound provider: it re-discovers the exact local model or confirms the exact
remote provider/model remains configured immediately before dispatch. The
validator returns short-lived capability and privacy evidence. Remote inference
remains limited to explicitly public workspaces; private work remains local.

Other router construction paths must configure their own trusted validator
before claiming fresh provider availability verification. Provider discovery
is not proof of provider quality or independent security review.

## Independent evaluation and deployment gates

AUREN contains sealed-arena exchange, replay and signature-verification
primitives. They can verify externally signed outcomes bound to a sealed
request, nonce and custody provenance. This does not make a local evaluator
independent: production independence requires separately controlled evaluator
identity, holdout custody, signing keys and deployment boundary. Private
signing keys and holdout answers must remain outside AUREN.

Remaining validation includes persistent/distributed cache isolation,
cross-host authorization and invalidation, trusted provider inventory across
all runtime entry points, external evaluator custody, replayable measured
benchmark campaigns, and exact-head CI for each integration change. Do not
claim production savings or independent evaluation until those gates pass.
