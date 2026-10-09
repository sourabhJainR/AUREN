# Grilling, Rework, and Decision Integrity

## Purpose

Make shared understanding and evidence-backed rework part of AUREN's default engineering lifecycle, not optional prompts. This policy adapts the principles of the open-source Grilling skill (https://github.com/mattpocock/skills/blob/main/docs/productivity/grilling.md) into AUREN's provider-neutral orchestration and evidence gates. It does not copy or depend on that skill at runtime.

## The grilling contract

Before a consequential change, build a decision tree for the task: goals, non-goals, observable acceptance criteria, constraints, interfaces, failure modes, rollout/rollback, and the definition of done. Ask the user only for decisions that belong to them; resolve factual questions through repository inspection, tests, documentation, or bounded research.

- Ask questions in **dependency-aware rounds**: surface every currently answerable decision together, but do not ask downstream questions whose prerequisites remain undecided.
- Keep facts and decisions separate. Agents gather facts and present evidence; the user retains authority over product intent, material trade-offs, and consequential choices. An agent must not silently answer a user-owned decision.
- Give each question a clear identifier, context, viable options, trade-offs, and a recommended answer with rationale. The recommendation is advice, not consent.
- Record resolved decisions, their rationale, source/evidence, and affected contracts in the existing task context/decision records. Do not create a parallel memory store.
- When an answer changes an earlier assumption, reopen only the affected branch and its dependants.
- Unresolved decisions block only tasks that depend on them. Continue independent, safe work; never use uncertainty as permission to invent intent.
- Before acting on a material plan, summarize the shared understanding, acceptance criteria, open risks, and stopping condition. Obtain confirmation when required by the task's authority/safety policy. Ordinary low-risk, well-specified tasks should not be slowed by ritual grilling.

## Evidence-led implementation and rework

Use this lifecycle for substantial changes:

`intent -> dependency-aware grill -> plan -> implement smallest verifiable slice -> adversarial review -> rework -> verify -> accept or re-plan`

1. Make acceptance criteria falsifiable and map each criterion to implementation and verification evidence.
2. Implement in small slices; preserve the existing architecture, public contracts, and unrelated work.
3. Review from perspectives independent of the author: correctness, omitted requirements, edge cases, security/privacy, compatibility, operational behavior, tests, and rollback.
4. If review finds a defect, **rework the implementation, not the verdict**. State the failed criterion, evidence, root-cause hypothesis, changed approach, and regression test. Re-run the relevant review and verification after the fix.
5. Do not treat a reviewer disagreement as resolved merely by rewriting the summary, weakening the test, or changing a pass/fail label. Any acceptance-criterion change needs an explicit rationale and the proper owner’s approval.
6. Record outcomes in the canonical Engineering State Ledger/evidence lineage. Distinguish observed facts, inference, and unverified claims.

## Anti-loop and recovery contract

- Reproduce and classify the failure before retrying; capture the exact failure signature and a falsifiable hypothesis.
- Allow at most two attempts with the same failure signature and hypothesis. Then change the hypothesis/approach, reduce scope, request the missing user decision, or stop as blocked with evidence.
- Enforce durable total-attempt and elapsed-time budgets through `portable.engineering_recovery.EngineeringRecoveryLedger`. Never retry indefinitely or claim progress without a changed artifact or new evidence.
- Keep checkpoints tied to the exact source/head SHA. CI is verification only when all required checks have completed successfully on the exact current head; stale, running, failed, missing, or skipped required checks are not green.
- On failure, preserve the last verified state and use the repository's safe rollback path. Continue unrelated tasks when safe and dependency-independent.
- Independent candidate competition and adversarial review can inform decisions, but do not override user authority, Guard policy, access control, or mandatory tests.

## Completion criteria

A task is complete only when acceptance criteria are traced to evidence, required tests and exact-head CI pass, meaningful review findings are resolved or explicitly accepted by the authorized owner, persisted state is consistent, and remaining limitations are reported honestly. If verification is blocked, report that explicitly; do not label the work complete.
