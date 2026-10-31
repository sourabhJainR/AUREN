# StateGraph lifecycle observability

The canonical StateGraph supervisor now emits separate lifecycle spans for configured node execution, evaluator verdicts, and repair attempts.

## Event contract

- Each node execution span records its attempt number and configured attempt ceiling. It records only a bounded terminal outcome and error type, not the node's full input or output.
- A configured evaluator emits a sibling span under the canonical supervisor trace. Its outcome is `ok`, `rejected`, or `error`; a rejection is not a pass.
- A configured repair emits a sibling span under the same supervisor trace. The span records whether a repair candidate was returned or the repair failed.
- Lifecycle coverage reports configured and observed evaluator/repair counts. It explicitly records that provider-tool adapter spans and external learning persistence are not instrumented by this StateGraph layer.
- Existing canonical run status, partial-dependency identity, run/task/session IDs, source revision and config digest remain the source of execution truth.

## Evidence boundary

These spans show what the StateGraph supervisor observed. They do not prove provider-side execution, external learning persistence, evaluator independence, sealed holdout isolation, production deployment, or release authorization. The trace is explanatory telemetry; it does not grant authority or change acceptance policy.

## Validation

The live regression covers a rejected first evaluator verdict, a repair candidate, a successful second attempt, exact span parentage and coverage counts. Existing redaction and trace integrity checks remain required.

## Rollback

Revert the StateGraph span wrappers and their regression together. Canonical graph execution, evaluator decisions, repair behavior and Guard policy remain unchanged.
