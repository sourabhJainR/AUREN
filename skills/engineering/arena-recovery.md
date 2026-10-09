# Arena-Informed Engineering Recovery

Use this flow for high-risk implementation, repeated CI failures, architectural uncertainty, or tasks that have failed twice without a verified change in diagnosis. Routine changes should use the smallest normal engineering workflow.

## Candidate competition

1. Write one standalone task brief with the exact goal, constraints, non-goals, repository evidence, acceptance tests, and stop condition.
2. Generate a small bounded set of independent implementation hypotheses. Each receives the same brief; vary the reasoning lens, not the requirements.
3. Have an independent reviewer attack each candidate for concrete correctness errors, missing requirements, counterexamples, security regressions, and compatibility breaks.
4. Require each candidate to answer every attack with evidence, concede real defects, and revise its plan.
5. Score candidates against a fixed rubric: correctness 30%, completeness 25%, robustness to attacks 20%, specificity 15%, clarity 10%. A verified fatal defect disqualifies a candidate. Do not choose by confidence or verbosity.
6. Implement only the selected, reviewed approach. A tournament winner is a hypothesis, not proof; repository-native tests remain authoritative.

## Bounded recovery

Use portable.engineering_recovery.EngineeringRecoveryLedger for durable attempt accounting and checkpoints. Initialize a run with an immutable task ID and intent. Record the exact failure signature, hypothesis, action, commit SHA, outcome, and evidence for each attempt.

- Two attempts with the same phase, failure signature, and hypothesis are the limit.
- After the limit, change the hypothesis or implementation path; never replay the same fix unchanged.
- Total attempts and wall-clock time are also bounded. Exhaustion ends in blocked with evidence, not an infinite loop.
- Store the last verified phase, exact head SHA, next action, and evidence in a checkpoint so a new process can resume safely.
- Use verify_ci_gate to reject queued/running CI, stale-head results, failed conclusions, missing required checks, and skipped required checks.
- Preserve unrelated work when one dependency is blocked; only dependent work waits.
- Capture a baseline, run focused tests then regression tests, review the diff, retain rollback, and re-run post-merge checks before advancing.

Arena output never grants authorization. Guard, repository permissions, human approval requirements, and the existing evidence/provenance stores remain authoritative.
