# Structured requirement conflict gate

The analyzer accepts typed facts from a trusted requirements parser. It does not classify arbitrary natural-language statements. This separation avoids treating keyword matches as proof that requirements conflict.

The initial rules cover six material conflicts in issue #1225: real-time versus delayed quotes; public redistribution versus provider prohibition; no accounts versus per-user saved portfolios; fully automated publication versus mandatory human approval; no external storage versus historical analytics; and fast MVP timing versus broad coverage, high availability and extensive compliance.

A conflict includes the exact source statements, impact, explicit options, trade-offs and a recommended option. The recommendation is not an automatic decision. Workstreams are blocked only when their declared requirement dependencies include a conflicted key; independent work can continue.

A decision record binds the selected option, decision maker, rationale, prior plan digest and new plan revision. It is deterministic and tamper-evident. The host must persist it through the existing canonical plan/evidence store and create the new plan revision before affected work resumes. This module does not create a second store, grant approval authority or persist decisions by itself.

## Integration boundary

The planner/requirements parser must emit RequirementFact values with stable source statement IDs. The host calls RequirementConflictAnalyzer.analyze before scheduling dependent work and routes every decision_required result to the existing decision/approval path. It must not silently pick recommended_option.

## Verification and limitations

The tests cover the six known contradiction classes, dependent versus independent workstreams, stable conflict identity and plan-bound decision records. This is structured-input coverage. It does not prove natural-language conflict extraction, persistent decision-store integration or end-to-end workflow orchestration; those remain part of #1225.


An empty or unavailable extraction result is reported as unscored, not clear. The caller must not treat a missing requirements parse as evidence that the plan has no conflicts.


Conflict matching is scoped. Opposing facts about different products, user groups or delivery phases do not conflict unless the trusted parser assigns them the same scope. The default scope is global.
