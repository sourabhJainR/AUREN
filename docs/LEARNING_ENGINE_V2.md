# Learning Engine v2

The learning system is now a closed decision loop rather than a telemetry heuristic.

## Lifecycle

`experience -> candidate scoring -> task-family regression selection -> replay -> shadow -> staged canary -> promotion -> monitoring -> rollback`

### Experience store

`.ai-harness/runtime/experience_store.py` stores structured outcomes in the local SQLite database configured by `[learning].experience_store`. The store is indexed by task family, strategy, policy and transfer key and survives individual runs.

### Candidate scoring

`.ai-harness/runtime/learning_engine.py` groups history by task family and strategy. The score combines verified quality, acceptance, safety, regression absence, retry/cost efficiency and a Wilson confidence lower bound. Sparse evidence is penalized instead of allowing a single successful task to replace the incumbent.

### Task-family regression selection

`.ai-harness/runtime/regression_selector.py` prioritizes known failures and recent failures from the same task family, then representative same-family cases, and finally neighboring families when the bounded corpus needs more coverage. Selection is deterministic and fingerprinted.

### Shadow and canary

`.ai-harness/runtime/canary_evaluator.py` runs candidates without activating them. The staged canary progresses through the configured exposure schedule only when every stage satisfies the pass and verification gates. The first failed stage halts the rollout.

### Promotion and rollback

`.ai-harness/runtime/learning_controller.py` requires regression replay, shadow validation, staged canary success, acceptable risk and sufficient candidate confidence before promotion. `.ai-harness/runtime/policy_registry.py` records parent lineage and active/superseded/rolled-back states. `.ai-harness/runtime/rollback_controller.py` detects acceptance degradation or regression increases and restores the previous active policy.

## Safety boundary

Learning can propose changes to routing, retrieval, graph topology, retry strategy and other executable orchestration behavior, but it cannot grant itself credentials, permissions or security exemptions. Candidate execution remains outside the learning store and must happen through the designated evaluation boundary.

## Configuration

The production-oriented defaults are intentionally conservative: minimum observations 5, candidate confidence lower bound 0.70, minimum improvement 0.03, and staged canary exposure of 5%, 10%, 25%, 50%, and 100% with 100% pass and verification gates.

## Evidence model

The confidence calculation uses a Wilson lower bound for a binomial success proportion. This is an uncertainty-aware guard against promoting a policy on a small lucky sample. AUREN should treat this as one gate among several, not as a guarantee of correctness.

The design also follows continual-learning and long-horizon maintenance principles: preserve history, evaluate by task family, replay known failures, and monitor behavior after promotion.
