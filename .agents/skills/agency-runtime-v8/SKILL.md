# Agency Runtime v8 — AI Coding Orchestrator Integration

Use the Agency Runtime as the control plane for substantial coding work.

## Contract
The AI coding orchestrator owns provider/tool execution, permissions, repository mutation, concurrency, and side effects. Agency Runtime owns task profiling, specialist planning, evidence, verification metadata, provenance, artifact regression, and release gating.

## Required flow
`profile -> route -> plan -> execute(host) -> record evidence -> verify -> regression -> release`

Before substantial work preserve:
`GOAL | NON-GOALS | REQUIREMENTS | CONSTRAINTS | PROTECTED BEHAVIOR | BOUNDARIES | ACCEPTANCE | RISKS | ASSUMPTIONS | intent_digest`.

## Specialist use
Select one primary specialist whenever possible. Add support/reviewer specialists only when independent expertise changes the result or a material risk requires it. Specialist output is work product, not proof of completion.

## Evidence
Every material claim must have evidence or be explicitly marked uncertain. Record source, claim, and locator where available. Do not place secrets or sensitive payloads in provenance details.

## Verification
Use the normal AUREN order:
`syntax/static -> focused tests -> integration/system -> regression replay -> security/policy -> final diff review`.
The orchestrator must report incomplete checks instead of converting them into success.

## Regression
Provide baseline and post-change artifact snapshots for substantive changes when available. Unexpected added, removed, or changed artifacts fail the release decision unless explicitly allowed by task policy.

## Provenance
Persist the run trace through `portable.agency_provenance.ProvenanceLedger`. Verify the chain before treating the run as reusable evidence.

## Release
Use `portable.ai_coding_agency_bridge.run_coding_task`. A run is ready only when the quality receipt, hard gates, findings, artifact regression, and release policy all pass.

## Security
The bridge never executes commands and never grants permissions. Tool calls, credentials, network access, mutation ordering, and external side effects remain host responsibilities.
