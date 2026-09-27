# Graph topology and governed regression growth

## Graph constructs adapted from Graph Engineering

HWS keeps the existing StateGraph as the single execution authority. The useful constructs added are:

- Node contracts: optional bounded input/output keys via NodeContract.
- Edge data contracts: add_edge with data_keys verifies that declared data is produced and consumed.
- Failure containment: parallel execution can opt into parallel_failure_mode="isolate" so one independent node produces a failure event without erasing successful siblings. The default remains fail-fast.
- Convergent cycles: ConvergenceGuard deduplicates against every observed item, not only confirmed items, and stops after a bounded number of dry rounds.
- Existing conditional routing and parallel supersteps remain the canonical graph runtime; no second workflow engine is introduced.

These constructs are deliberately additive. They do not bypass evidence, verification, security, resource or promotion gates.

## Episode-driven regression lifecycle

A completed or verified failed EngineeringEpisode now feeds a persistent RegressionCorpus during maintenance:

verified episode -> deterministic fingerprint -> candidate case -> independent replay -> repeated validation -> active case

### Promotion

A candidate becomes active only after two independent passing validations with evidence. A validation must carry evidence IDs and be explicitly independent.

### Strengthening

If another verified episode has the same deterministic fingerprint, it strengthens the existing case instead of creating a duplicate. New evidence and episode lineage are accumulated.

### Failure protection

A failed episode creates a high-risk prevention case describing the verified failure class and its DO/DON'T memory. A failed validation resets consecutive passes; an active case returns to candidate until independently revalidated.

### Retirement

Cases are never retired because they are old or inconvenient. Retirement requires an active replacement case and explicit retirement evidence. The old case is marked superseded and retains lineage.

### Safety boundary

Regression creation is independent from SkillOpt success. If a skill has no usable holdout, the episode can still create a candidate regression case. This keeps regression protection stronger than the availability of any particular learning experiment.

The checked-in .ai-harness/regressions/corpus.jsonl remains the deterministic repository baseline. The runtime RegressionCorpus is the durable project-local growth layer and can export active/candidate cases back to JSONL for review or corpus refresh.
