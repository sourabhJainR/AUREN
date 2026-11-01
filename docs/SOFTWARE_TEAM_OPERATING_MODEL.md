# AUREN software-team operating model

AUREN executes engineering work as a supervised software organisation, not as an unstructured collection of independent chat agents. The canonical graph crew catalogue now includes solution architect, engineering manager, research/evaluation, release/operations, CTO sponsor, and CEO sponsor contracts alongside the existing specialist crews. `build_software_team_persona_adapters` creates their proposal adapters with explicit per-persona read-only tool scopes; `GraphAgentTeam.required_personas()` now surfaces manager, architect, research, release, CTO, and CEO personas for matching goals. Registration of provider-backed planners and per-role tool scopes remains an explicit host configuration step. Graph/crew execution is the work graph; the supervisor owns lifecycle, authority, durable state, budgets, deadlines, cancellation, recovery, and final acknowledgement.

## Organisation

| Persona | Primary responsibility | Must produce |
|---|---|---|
| CEO / product sponsor | Set business outcome, priority, risk appetite, and stakeholder commitments | Approved goal and success measures |
| CTO / chief architect | Own technical direction, system boundaries, architecture decisions, and technical risk | Architecture decision and technical acceptance criteria |
| Engineering manager | Own delivery plan, staffing, dependency graph, budget, deadlines, escalation, and release readiness | Executable work plan and delivery decision |
| Solution architect | Decompose the goal into bounded work packages and contracts | Architecture, interfaces, dependency map |
| Backend / platform engineer | Implement services, runtime, adapters, and integrations | Code change, implementation notes, test evidence |
| Frontend / product engineer | Implement operator experience and user-facing workflows | UI change, accessibility and interaction evidence |
| QA / verification engineer | Independently test requirements, regressions, recovery, and holdouts | Reproducible test report and unresolved defects |
| Security / reliability engineer | Review authority, isolation, data boundaries, failure handling, and operational recovery | Threat findings, policy tests, recovery evidence |
| Research / evaluation engineer | Compare candidate approaches and measure capability changes | Baseline/candidate report, transfer and uncertainty results |
| Release / operations engineer | Validate packaging, deployment, observability, rollback, and production signals | Release checklist and rollback proof |

## Execution graph

`Stakeholder goal -> CEO/CTO intent -> Engineering manager plan -> Architect decomposition -> specialist crew -> independent QA + security -> manager acceptance -> release/operations -> stakeholder report`

- The engineering manager creates a dependency-aware graph and assigns specialist roles. Parallel work is allowed only where contracts and shared budgets permit it.
- The architect owns design intent; implementation agents cannot approve their own architecture changes.
- Backend and frontend engineers own implementation evidence, not final correctness claims.
- QA is a separate role with independent acceptance criteria and the authority to block completion.
- Security/reliability review can block work when authority, isolation, or recovery evidence is missing.
- Research/evaluation compares candidates against a pinned baseline and sealed holdouts. A better score on development tasks alone is not a promotion decision.
- The manager may acknowledge delivery only after required specialist outputs, independent QA, security gates, durable receipts, and deadline/budget checks pass.
- CEO/CTO receive concise status, material risks, decisions required, and evidence links. Stakeholder reporting must distinguish verified outcomes from estimates and unknowns.

## Runtime contracts

1. Every work unit has a stable identity, parent, owner persona, acceptance criteria, budget allocation, deadline, and cancellation token.
2. Shared crew budgets are enforced at the real execution and delivery boundaries, not only in the planner.
3. Checkpoints and evidence are durable and provenance-bound. Restart must not duplicate acknowledged side effects.
4. A provider timeout, lost receiver, missing final acknowledgement, stale lease, or failed recovery is never reported as success.
5. Retries are bounded and require new evidence or a changed execution condition.
6. A role handoff carries the minimum required context plus immutable references to source artifacts and decisions.
7. The dashboard reads canonical supervisor/session state. It must not infer completion from agent prose or UI-local state.
8. Human approval remains required for production authority changes, irreversible actions, policy exceptions, and release gates that explicitly require an operator.

## Persona adaptation

Each persona uses a different working mode:
- Architect: broad context, options, constraints, dependency and risk analysis.
- Implementer: narrow task scope, exact files/contracts, test-first execution, concise context.
- QA: fresh context, independent acceptance criteria, adversarial and negative tests.
- Security reviewer: threat model, authority boundaries, bypass paths, and fail-closed checks.
- Research engineer: controlled comparisons, uncertainty, reproducibility, and transfer.
- Engineering manager: progress, dependencies, budgets, deadlines, blockers, and decisions.
- CEO/CTO: outcomes, strategic risk, investment choices, and evidence quality.

A persona is a task policy and evaluation profile, not a claim that the underlying model has a stable human identity. AUREN should select model, tools, memory, and context by measured task performance and policy eligibility.

## Capability improvement loop

The independent [AUREN Capability Lab](https://github.com/sourabhJainR/firstTestproj) is used to prototype and measure new software-team and reasoning capabilities. It must remain decoupled from AUREN runtime. Candidate capabilities enter AUREN through a reviewed PR with:
- baseline and candidate on identical development and holdout tasks;
- correctness, security, calibration, transfer, latency, memory, and cost evidence;
- explicit unknown/unmeasured fields;
- regression, fault-injection, and restart/cancellation tests;
- rollback and operator acceptance where required.

This operating model supports the long-term SI and AGI research direction. It does not assert AGI achievement. Claims about generality must be backed by broad, independent transfer evidence and external evaluation.
