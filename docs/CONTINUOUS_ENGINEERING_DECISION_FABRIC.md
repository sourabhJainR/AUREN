# Continuous Engineering Decision Fabric

The fabric makes routing adaptive using measured outcomes while remaining safe in
real multi-team repositories.

It learns duration, cost, success/failure and quality; selects provider/tool
paths; adjusts verification depth for impact and repository risk; compares
counterfactual candidates; and gates promotion with canaries.

Repository handling is explicitly branch-aware. A read-only guard records the
active branch, HEAD/base SHAs, dirty files and ahead/behind divergence. It never
silently switches branches, pulls, merges, resets or discards another team's
work. The host must explicitly synchronize or switch branches.

Legacy constraints are represented as repository/task metadata and are inputs
to the decision rather than assumptions to overwrite. This lets the same
control plane operate against legacy and modern codebases.

Promotion is data-only here. Rollback_target is persisted, but the actual
rollback/mutation stays with the existing execution authority.

Flow:
repository snapshot -> legacy/impact constraints -> route candidates ->
historical evidence -> decision -> execution -> verification -> outcome ->
routing feedback -> counterfactual -> canary -> promote/rollback -> repeat.
