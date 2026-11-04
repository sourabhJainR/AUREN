# Shared team budget at provider and tool boundaries

Assigned agent calls to both provider and tool adapters use the same host-installed `TeamBudgetAdapterEnforcer` instance from the approved tool registry.

For a provider call, the adapter passes only bounded metadata to the context resolver: task ID, crew ID, pinned provider version and prompt digest. It does not pass the prompt text as reservation metadata. The host resolver binds the active authenticated principal to the canonical task/run/member, stable operation key and reservation ceilings.

The enforcer reserves shared team budget before the provider call. It settles usage through the host receipt resolver and the controller's independent settlement verifier before the provider result can be used. A timeout, provider exception, unknown result or failed settlement leaves the reservation held for explicit reconciliation. A late provider response is not replayed automatically.

If an authenticated assignment is active and no enforcer is configured, the provider adapter denies before invoking the provider. Unassigned direct calls remain compatible when no enforcer is installed. When an enforcer is installed, assigned context is required.

The provider-backed specialist and software-team builders inherit the same enforcer from `ApprovedToolRegistry`, so provider calls and approved read-only tool calls share one durable ledger. This does not authorize side-effecting tools or expand the assigned resource scope.
