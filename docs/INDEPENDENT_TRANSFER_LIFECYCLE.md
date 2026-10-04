# Independent Transfer Learning Lifecycle

AUREN now has one fail-closed protocol for evaluating whether a learned capability
really transfers beyond the domain that produced it.

Pipeline:

`independent generation -> sealed execution -> cross-domain measurement -> causal attribution -> fresh holdout retest -> promotion/rollback`

## Boundaries

- The benchmark generator is a distinct principal from the runtime.
- The oracle remains external to the runtime and is identified in the sealed manifest.
- Cases contain opaque task/input/environment digests, never benchmark answers.
- Target-domain measurements require sealed holdout observations.
- Causal attribution requires randomized control/treatment evidence and an independent holdout.
- Promotion records a versioned policy artifact only. It does not grant permissions, credentials, merge authority, or execution rights.
- A failed causal, transfer, or holdout gate rolls back to the prior promoted version when one exists.

## Evidence

`portable.independent_transfer_learning_lifecycle` composes the existing benchmark manifest, sealed Arena boundary, run receipt, causal promotion gate, and retest contract rather than creating competing owners.

The generated benchmark is content-addressed. A sealed run receipt must match both corpus and manifest digests. Transfer is measured separately for the source domain and at least two independent target domains. The causal estimator reports a bounded treatment effect and evidence confidence; it does not claim that a statistical estimate proves causality in every deployment.

## Example flow

1. An independent generator creates opaque cases and a sealed train/holdout manifest.
2. The existing external evaluator executes the sealed request.
3. A trusted run receipt is checked against the benchmark lineage.
4. Control and treatment outcomes are measured on independent target holdouts.
5. Randomized treatment effect is estimated with an independent holdout.
6. A new `CampaignRetestContract` is created before activation.
7. Passing evidence promotes a versioned capability artifact.
8. A failed later evaluation restores the previous promoted version.

The lifecycle is evaluation infrastructure, not an autonomy-permission mechanism.