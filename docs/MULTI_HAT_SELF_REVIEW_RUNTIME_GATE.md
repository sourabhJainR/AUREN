# Multi-Hat Self-Review Runtime Gate

The continuous engineering loop now supports a post-implementation self-review gate.

Every completed implementation can be reviewed through seven independent hats:

- Architectural
- Senior implementation
- Senior quality
- Senior security
- Senior performance
- End user
- PM / stakeholder

The review runs after verification and before learning/promotion.

## Decision contract

The reviewer returns a SelfReviewReport.

- No findings: the loop may continue.
- Findings + stop: execution terminates with self_review_stop.
- Findings + address: execution terminates with self_review_address; findings are persisted in the remediation backlog.
- Findings + accept: the developer explicitly accepts the findings and the loop may continue.

Reviewers cannot mutate the repository, promote capabilities, or override execution authority.

Every finding receives a stable semantic ID and is persisted as durable remediation work with its hat, severity, recommendation, and evidence references. This means review findings become future engineering constraints rather than disappearing at the end of an episode.

The continuous runtime exposes the same gate through its optional self_review callback.

## Safety property

The default path is unchanged when no self-review callback is supplied. When a review callback is supplied, findings are fail-closed until the developer makes an explicit decision.
