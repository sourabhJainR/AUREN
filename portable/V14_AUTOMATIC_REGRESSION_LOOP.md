# AUREN v14: Automatic Trace Regression Loop

AUREN v14 closes the engineering feedback loop without making an external observability or evaluation service mandatory.

## Closed loop

```text
code change
   |
   v
execution trace
   |
   +--> deterministic quality receipt
   |
   +--> AUREN creates regression run
   |       |
   |       +--> versioned dataset
   |       +--> dataset digest
   |       +--> per-case evaluation
   |       +--> aggregate scores / failures
   |
   +<-- regression correlation
   |
   v
promotion policy
   |
   +--> rollback  (hard gate or regression failure)
   +--> shadow    (quality below release threshold)
   +--> canary    (staged rollout policy)
   +--> promote   (quality + regression pass)
```

## API

`RegressionPlan` defines the versioned regression corpus and the deterministic functions AUREN must run. `execute_regression()` creates an AUREN-owned run ID and executes the complete dataset. The result retains the dataset content digest from `Dataset.digest`.

```python
from portable.agency_observability import Dataset, DatasetItem
from portable.agency_regression_loop import RegressionPlan, PromotionPolicy
from portable.agency_runtime import execute

plan = RegressionPlan(
    dataset=Dataset(
        "coding-regression", "14",
        (DatasetItem("input", "expected"),),
    ),
    execute_case=lambda trace, value: candidate(value),
    judge=lambda trace, item, output: {
        "correctness": float(output == item.expected),
    },
)

result = execute(
    task,
    worker,
    regression_plan=plan,
    promotion_policy=PromotionPolicy(quality_threshold=0.90),
)

print(result.trace_id)
print(result.regression_run.regression_id)
print(result.regression_run.result.dataset_digest)
print(result.promotion_decision.action)
```

## Decision semantics

The default policy is deliberately conservative:

1. Any failed hard gate -> `rollback`.
2. Any required regression failure -> `rollback`.
3. Regression passes but quality is below the promotion threshold -> `shadow`.
4. Quality and regression pass -> `promote` by default.
5. Set `promote_on_pass=False` to route a passing candidate to `canary` instead of immediate promotion.

The decision is advisory state for the release controller. It does not mutate source code, deploy an artifact, bypass safety gates, or perform an irreversible rollback itself.

## Trace correlation

The regression run is correlated to the parent trace with the existing v13 `RegressionLink`. The trace therefore carries both the exact dataset digest and the resulting promotion decision. This makes a later audit able to answer:

- which engineering execution produced the candidate;
- which regression run validated it;
- which exact dataset version and digest were used;
- which metrics failed or passed; and
- why the candidate was promoted, staged, shadowed, or rolled back.

## Compatibility and safety

The existing `regression_result` argument remains supported for callers that already own a regression engine. `regression_plan` is the new automatic path. The two modes are mutually exclusive.

The implementation is dependency-free and offline-capable. Regression execution is deterministic only to the extent that the supplied `execute_case` and `judge` functions are deterministic; AUREN records their resulting evidence but does not claim external reproducibility.
