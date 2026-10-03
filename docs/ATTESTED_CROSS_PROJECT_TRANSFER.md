# Attested Cross-Project Transfer

This phase makes cross-project generalization an explicit evidence gate.

A transfer observation counts only when it:
- crosses distinct source and target projects;
- is a holdout;
- uses an independent oracle;
- points to a trustworthy external evaluation attestation;
- belongs to one project pair and one capability.

The default gate requires five observations, >=80% transfer success, and zero
regressions. The evaluator is analysis-only and cannot copy or promote a
capability.

This is stronger empirical evidence, not proof of AGI. The target project must
remain independently evaluated rather than being another self-test of the
source repository.
