# Engineering plan goal reconciliation

`EngineeringPlanGoalReconciler` is a stateless callable for one canonical EngineeringPlan run. It is designed to be registered with the continuous supervisor under its stable `goal_key`.

## Host composition

Create the canonical `ExecutionController`, `EngineeringPlanStore`, `EngineeringPlanRecoveryBridge`, `DurableApprovalStore` and `TrustedPlanDispatchRoot` first. Then create the reconciler with host-owned callbacks:

- `handler_resolver(plan, task)` selects an already registered handler.
- `payload_resolver(plan, task)` creates the bounded task payload and resource selectors.
- `budget_resolver(plan, run, task)` reads the live canonical budget and returns packet budget limits.
- `completion_verifier(plan, task, durable_task)` verifies the task's actual acceptance evidence and returns `{verified: true, evidence_refs: [...]}` only when the evidence is valid.
- `policy_constraints` supplies the non-empty policy constraints for each fresh context packet.

Register the callable as `{reconciler.goal_key: reconciler}` in the continuous supervisor's `goal_reconcilers` mapping. The reconciler does not create a second queue, plan store, recovery journal or authority boundary.

## Reconciliation sequence

1. Load the plan and run through `EngineeringPlanRecoveryBridge.load_verified()`.
2. Recover an active task by its deterministic work key through `ExecutionController.task_by_work_key()`.
3. Wait while the durable task is pending or running.
4. Verify completed work through the host completion verifier. A worker's completed state alone is not acceptance evidence.
5. Move accepted work through `running -> verifying -> passed` and persist its evidence references.
6. Promote only dependency-ready work, then persist `ready -> running` before building the immutable context packet.
7. Dispatch through `TrustedPlanDispatchRoot` with the stable work key. Do not advance the plan/run revision after queue insertion while the worker is active.
8. Report parent completion only when every plan task is passed with evidence.

## Recovery and failure rules

- The reconciler has no in-memory progress. A fresh instance recovers task identity from the controller database.
- If dispatch acknowledgement is lost, the next cycle looks up the same work key. It never creates a second task for the same unit.
- A missing durable task for a running unit is retried with the same key and plan/run revision.
- A failed or cancelled worker is recorded as failed/cancelled. It is not automatically re-enqueued.
- Missing or unverified completion evidence returns `waiting_external_dependency`; the supervisor holds the goal until new evidence is supplied and an authenticated operator wakes it.
- A blocked/failed plan requires explicit replan evidence and a higher replan generation before retry.
- Assignment, approvals, shared budgets, policy and handler authority remain in the trusted root and controller. Reconciler callbacks cannot grant authority.

## Test coverage

`tests/test_engineering_plan_goal_reconciler.py` verifies dependency-aware two-unit continuation, recovery by stable work key from a fresh reconciler instance, evidence-gated completion, and refusal to mark work passed without verified evidence.

This is application-level evidence. It does not prove production OS isolation, independent evaluator custody or physical multi-host staging.


## Assignment revalidation after restart

The controller's durable preflight request does not contain the original in-memory context packet. The trusted root therefore reconstructs the narrow `packet.task` view from `EngineeringPlanRecoveryBridge.load_verified()` before calling the host assignment resolver. The host resolver still reauthenticates the principal and manager and recomputes policy, role, scope, action class, deadline and task-payload bindings. It does not trust task identity or role fields supplied by the worker. The regression clears the root's private dispatch-context cache before resuming a queued task, simulating process restart while retaining only canonical durable state.
