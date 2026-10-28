# AUREN AI Engineering Organization Contract

**Status:** proposed organizational contract; not a runtime authorization policy or production certification.

AUREN is intended to operate as an AI-native engineering organization. Named agents have stable designations, titles, expertise, reporting relationships, accountabilities, permitted action classes, prohibited actions, and approval boundaries. This is a contract for planning and evaluation; it does not by itself enforce access or authority.

The machine-readable registry is [`config/agent_org.v1.json`](../config/agent_org.v1.json). Regression checks are in [`tests/test_agent_org_contract.py`](../tests/test_agent_org_contract.py).

## Reporting structure

```text
Stakeholders and Investors
└── CEO-01 — Chief Executive Agent
    ├── FIN-01 — Finance and Investor Reporting Agent
    └── CTO-01 — Chief Technology Agent
        ├── SEC-01 — Security Engineer Agent
        └── EM-ENG-01 — Engineering Manager Agent
            ├── BE-01 — Senior Backend Engineer Agent
            ├── ARCH-01 — Principal Architect Agent
            ├── QA-01 — Senior QA Engineer Agent
            └── SRE-01 — Reliability Engineer Agent

Independent Governance Council (external assurance owner)
└── EVAL-01 — Independent Evaluation Agent
```

The evaluator is deliberately outside the implementation reporting line. A separate name or process is not sufficient proof of independence; custody, infrastructure, credentials, corpus and evaluator identity must be independently controlled.

## Operating contract

1. **CEO:** sets portfolio priorities and business outcomes; reports to stakeholders and investors using verified metrics.
2. **CTO:** owns technical standards, architecture governance, security posture and release recommendations.
3. **Engineering manager:** creates dependency-aware plans, assigns scoped work, manages shared budgets, and escalates blockers.
4. **Specialist agents:** backend engineering, architecture, QA, reliability and security deliver scoped artifacts and evidence.
5. **Independent evaluator:** checks pinned artifacts against independent criteria; cannot modify the candidate to make its own evaluation pass.
6. **Finance/reporting:** computes cost and ROI from retained records, including failures, skipped checks, rework and uncertainty.

## Runtime requirements before enabling this hierarchy

- Keep the canonical supervisor, `StateGraph`, and `ExecutionController` as the only execution authority. Do not create a competing orchestrator.
- Bind every task to a stable ID, role ID, manager, explicit scope, delegated authority, shared budget, deadline, checkpoint, durable status and next action.
- Enforce permissions at the real execution boundary. A role title, prompt, manager message, model confidence score or registry entry cannot grant access.
- Record waiting, working, blocked, failed and complete states distinctly. Completion requires the configured acceptance evidence.
- Keep side-effect intent and operation identity durable before dispatch. Unknown provider outcomes remain blocked until trusted reconciliation; retries are bounded and cannot silently duplicate consequential operations.
- Separate implementation, QA, security review and independent evaluation. A worker cannot approve its own consequential work.
- Derive CEO/CTO dashboards from durable task, test, provider, deployment and recovery records—not agent-written summaries.
- Preserve rollback owners, last-known-good revisions, evidence and a safe rollback procedure for every activated change.

## Initial acceptance gates

- Registry is valid JSON, role IDs are unique, reporting parents exist, and the hierarchy has no cycles.
- Each role declares expertise, accountabilities, allowed and prohibited action classes, and approval boundaries.
- CEO/CTO/manager roles cannot override mandatory gates; the evaluator is outside the implementation reporting chain.
- The existing controller remains canonical; this registry does not silently enable tools, adapters, model routes or extension packs.
- Exact-head CI and post-merge main checks are retained. Skipped checks remain skipped.
- Deployment enforcement, production isolation, independent evaluator custody and operator-run rollback remain **not verified** until separate evidence is attached.

## Rollback

This first slice is additive documentation and a descriptive registry. Revert the registry, documentation and its test together if the role contract conflicts with the runtime or repository governance. Do not enable runtime behavior based on this metadata alone.

## Status vocabulary

- **Implemented:** code or contract exists in the repository.
- **CI-verified:** the named contract passed on an exact revision.
- **Deployment-verified:** exercised in the intended deployment with retained operational evidence.
- **Independently verified:** separate evaluator or operator produced authenticated evidence.

No agent count, hierarchy diagram, green internal CI result, or role label establishes autonomous-company readiness by itself.
