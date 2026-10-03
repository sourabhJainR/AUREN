# External Curriculum Campaign Orchestrator

Phase 18 closes the gap between curriculum selection and execution.

The orchestrator consumes a frozen, trustworthy CurriculumPlan and asks an
external generator for a validation plan, environment contract, generator
digest, and oracle digest for every target. It delegates execution and
independent verification to CapabilityInventionValidationRunner.

It owns coordination and evidence lineage only. It does not create benchmark
answers, create an oracle, mutate capabilities, promote lifecycle state, or
silently reuse a changed curriculum plan.

Per-target failures are recorded and may be passed to an external recovery
adapter. Recovery never converts a failed target into success. The aggregate
campaign remains untrustworthy until every target has admissible evidence.

Evidence flow: CurriculumPlan -> external generator -> validation plan and
environment -> validation runner -> independent oracle -> CampaignOutcome ->
ExternalCurriculumCampaignResult.

This is an execution coordinator, not evidence of AGI. Stronger claims still
require independent external evaluation across genuinely unseen domains and
environments.
