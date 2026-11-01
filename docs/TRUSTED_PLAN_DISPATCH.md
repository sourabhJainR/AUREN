# Trusted plan dispatch composition root

TrustedPlanDispatchRoot is the only supported plan-to-worker dispatch path when a host has enabled durable plan execution. It resolves authority at the host boundary immediately before the dispatch intent is persisted.

## Host bootstrap

Create the canonical ExecutionController, EngineeringPlanStore, EngineeringPlanRecoveryBridge, and DurableApprovalStore first. The approval store must share the controller database. Then configure the root through AdaptiveRuntime.configure_trusted_plan_dispatch() or AurenClient.configure_trusted_plan_dispatch().

The host must provide two trusted resolver callbacks:

- budget_resolver(run_id, plan_task_id, handler) returns the live supervisor-owned RuntimeExecutionGuard, a stable budget ID, and the policy digest. It must retrieve the guard from the canonical supervisor checkpoint/ledger, not construct a fresh budget and not read budget claims from worker payloads.
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

The approval checkpoint must also bind the exact plan run ID, plan task ID, plan digest, run revision, and source revision from the immutable EngineeringContextPacket. The root checks the live budget checkpoint digest, cancellation/deadline state, approval status, current artifact/evidence/policy digests, audit-chain integrity, required approval count and separation of duties. It derives the approval binding from the canonical durable record and vote-event chain. This digest is content-bound audit evidence, not a detached cryptographic signature.

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
