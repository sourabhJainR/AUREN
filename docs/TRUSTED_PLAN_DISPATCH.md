# Trusted plan dispatch composition root

TrustedPlanDispatchRoot is the only supported plan-to-worker dispatch path when a host has enabled durable plan execution. It resolves authority at the host boundary immediately before the dispatch intent is persisted.

## Host bootstrap

Create the canonical ExecutionController, EngineeringPlanStore, EngineeringPlanRecoveryBridge, and DurableApprovalStore first. The approval store must share the controller database. Then configure the root through AdaptiveRuntime.configure_trusted_plan_dispatch() or AurenClient.configure_trusted_plan_dispatch().

The host must provide two trusted resolver callbacks:

- budget_resolver(run_id, plan_task_id, handler) returns the live supervisor-owned RuntimeExecutionGuard, a stable budget ID, the immutable admission checkpoint digest, and the policy digest. The checkpoint digest must be read from the canonical supervisor budget ledger and remain stable for idempotent replay after budget charges; the live guard snapshot is separately checked for cancellation/deadline validity. Do not construct a fresh budget or read budget claims from worker payloads.
- approval_resolver(run_id, plan_task_id, handler) returns the durable approval ID and the exact ActionRequest for that plan action. It must resolve using host-owned task identity and policy, not agent-supplied approver, scope, approval ID, or payload metadata.

Example host flow:

    runtime.configure_trusted_plan_dispatch(
        store=plan_store,
        recovery_bridge=plan_recovery_bridge,
        approval_store=durable_approval_store,
        budget_resolver=resolve_supervisor_budget,
        approval_resolver=resolve_exact_action_approval,
    )
    task = runtime.dispatch_engineering_plan_task(
        run_id=plan_run_id,
        task_id=plan_task_id,
        dispatch_key=stable_dispatch_key,
        handler=registered_handler,
        packet=fresh_context_packet,
        payload=task_payload,
    )

The approval checkpoint must also bind the exact plan run ID, plan task ID, plan digest, run revision, and source revision from the immutable EngineeringContextPacket. The root checks the live guard snapshot, binds the immutable supervisor admission checkpoint digest, and checks cancellation/deadline state, approval status, current artifact/evidence/policy digests, audit-chain integrity, request-row/expiry consistency with the original append-only request event, final approval state/version consistency, required approval count and separation of duties. It derives the approval binding from the canonical durable record and vote-event chain. This digest is content-bound audit evidence, not a detached cryptographic signature.

After root registration, direct calls to EngineeringPlanDispatch.dispatch() and the legacy ExecutionController.dispatch_engineering_plan_task() path fail closed. The AdaptiveRuntime and SDK facade also refuse dispatch until configured.

## Execution and review boundaries

- The graph/crew runtime remains the execution authority; the composition root only resolves bindings and creates the durable dispatch intent.
- Workers receive no authority to change the budget, approval, policy, or reviewer identity.
- QA and security review must run through independent fresh-context reviewers; an implementer cannot approve its own consequential operation.
- An approved record is not a completed external effect. The action gateway and durable side-effect ledger still own execution claim, provider outcome, reconciliation and final acknowledgement.
- Unknown remote outcomes remain blocked until the provider operation is authoritatively reconciled.

## Validation and rollback

tests/test_trusted_plan_dispatch.py covers approved/pending/changed-context cases, exact plan-task/revision binding, cancelled budgets, direct-dispatch bypass, and the AdaptiveRuntime bootstrap path. Existing tests/test_execution_controller_cancellation.py covers supervisor-to-graph-to-crew-to-model-router cancellation and absolute-deadline propagation. Existing crew handoff tests cover independent review receipts and rejection.

Rollback by reverting the root module, the dispatch token fence, AdaptiveRuntime/SDK integration, and the focused tests together. Retain existing task, plan, approval and recovery databases; no durable rows are rewritten or deleted.

Host bootstrap must install the root before any engineering-plan worker can be scheduled. A missing resolver, stale checkpoint, invalid approval chain, or absent approval blocks dispatch before queue insertion.


## Worker preflight and deadline bounds

The root checks the live supervisor guard before insertion and clamps the queued task deadline to the guard's remaining time. A caller may request a shorter deadline but cannot extend the canonical supervisor deadline. A missing, cancelled, expired or malformed guard blocks insertion.

The controller recomputes the complete dispatch binding immediately before handler execution, including the deadline policy, shared-budget receipt and durable approval receipt. Partial or tampered bindings fail closed. AdaptiveRuntime and AurenClient require the trusted root for all plan workers. The persisted trusted-binding fields also trigger the same fail-closed check on a low-level controller after restart, even when its in-memory root-required flag was not restored. Bootstrap the root and register the canonical recovery bridge before calling resume_pending. A plan task encountered before that bootstrap remains blocked.

This is still an application-level contract. It does not prove production OS isolation or independent evaluator custody.
