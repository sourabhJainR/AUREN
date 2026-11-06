# Structured requirement conflict gate

The analyzer accepts typed facts from a trusted requirements parser. It does not classify arbitrary natural-language statements. This separation avoids treating keyword matches as proof that requirements conflict.

The initial rules cover six material conflicts in issue #1225: real-time versus delayed quotes; public redistribution versus provider prohibition; no accounts versus per-user saved portfolios; fully automated publication versus mandatory human approval; no external storage versus historical analytics; and fast MVP timing versus broad coverage, high availability and extensive compliance.

A conflict includes the exact source statements, impact, explicit options, trade-offs and a recommended option. The recommendation is not an automatic decision. Workstreams are blocked only when their declared requirement dependencies include a conflicted key; independent work can continue.

A decision record binds the selected option, decision maker, rationale, prior plan digest and new plan revision. It is deterministic and tamper-evident. The host must persist it through the existing canonical plan/evidence store and create the new plan revision before affected work resumes. This module does not create a second store, grant approval authority or persist decisions by itself.

## Runtime integration

The canonical AdaptiveRuntime composition root accepts the host-owned requirement_conflict_resolver and conflict_resolution_verifier callbacks at construction or while configuring trusted plan dispatch. StateGraph forwards the same callbacks to Orchestrator in both traced and untraced execution. The resolver receives a detached copy of the bounded, sanitized intake receipt and the exact graph digest. It must return exactly:

- facts: RequirementFact values whose IDs use statement:<index> and whose statement text exactly matches a non-redacted intake statement;
- not_applicable_statement_ids: every remaining requirement, constraint, acceptance, assumption, or non-goal statement that does not map to one of the structured conflict dimensions;
- workstream_dependencies: a mapping for every graph node to the structured requirement keys it depends on (an empty list means independent of those dimensions).

The runtime verifies exact source binding and complete statement coverage. Missing, invented, redacted, partial, unknown-dimension, or malformed facts block execution before graph nodes run. The callback is installed by the trusted host before the first run; request payloads cannot install or replace it.

Detected conflicts are joined to graph nodes through the host-resolved dependency map. A conflict blocks only its dependent nodes and their descendants; independent nodes may continue after the host conflict_scope_verifier validates the complete mapping. A decision only unblocks affected work when the existing conflict_resolution_verifier confirms the exact conflict ID and selected option against the current durable decision record. The recommended option is never selected automatically.

The structured report is added to the existing pre-implementation review evidence and digest. This does not create a second scheduler, decision store, or authorization authority. The host remains responsible for writing a decision through EngineeringDecisionStore and advancing the canonical plan revision before resuming work.

When no host resolver is configured, the structured report explicitly says not_configured; the runtime does not describe the structured dimensions as fully analyzed. The existing natural-language design guard continues to run independently.

## Verification and limitations

The tests cover the six known contradiction classes, dependent versus independent workstreams, stable conflict identity and plan-bound decision records. This is structured-input coverage. It does not prove natural-language conflict extraction, persistent decision-store integration or end-to-end workflow orchestration; those remain part of #1225.


An empty or unavailable extraction result is reported as unscored, not clear. The caller must not treat a missing requirements parse as evidence that the plan has no conflicts.


Conflict matching is scoped. Opposing facts about different products, user groups or delivery phases do not conflict unless the trusted parser assigns them the same scope. The default scope is global.


Unknown requirement keys produce a partial result and are listed in unrecognized_keys. The caller must not report the plan as conflict-free until every relevant requirement dimension is mapped to a recognized rule set or explicitly marked not applicable by the requirements owner.


Unsupported values for a known dimension also produce a partial result. A key is considered fully analyzed only when each fact uses a value from that dimension's declared vocabulary.
