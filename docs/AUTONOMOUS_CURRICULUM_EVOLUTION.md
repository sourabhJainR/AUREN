# Autonomous Curriculum Evolution

Curriculum evolution selects the next independent evaluations from discovered
novel task/environment targets.

It:
- prioritizes novelty;
- excludes completed targets;
- applies a bounded target budget;
- preserves fresh-holdout and independent-oracle requirements;
- emits a deterministic curriculum digest.

It is proposal-only: execution, oracle generation, learning, and capability
promotion remain separate authorities.

Flow:

external outcomes -> task/environment discovery -> curriculum evolution
-> independent generation -> validation -> experiment -> causal gate -> canary
