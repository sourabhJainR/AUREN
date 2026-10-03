# Capability Invention Validation Runner

The runner turns an invention validation plan into executable, externally
verified evidence without granting the runner promotion authority.

Flow:

capability gap -> invention proposal -> validation plan -> isolated executor
-> independent oracle -> validation evidence -> causal/experiment pipeline

## Trust boundaries

- The validation plan is frozen and optionally pinned by digest.
- Every probe is a fresh holdout and requires an independent oracle.
- At least two task families are required to prevent single-family overfitting.
- The executor is supplied by the host; the runner does not own execution authority.
- Probe identity, environment digest, oracle agreement, and step budgets are checked.
- Contaminated runs fail closed.
- The runner emits evidence only. It never registers, promotes, deploys, or mutates a capability.

## Re-entry

ValidationEvidence binds the invention proposal, exact holdout plan, probes and
execution traces. Downstream orchestration can translate this evidence into a
control/treatment experiment and then into the existing cross-domain causal
promotion chain.

Production deployment should provide the executor and oracle from separate
processes or containers and keep benchmark corpus and oracle answers outside
the HWS trust boundary.
