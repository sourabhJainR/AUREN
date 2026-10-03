# External Evaluation Campaign Protocol

This protocol is the next trust boundary above the independent generalization
arena. It lets HWS consume evidence from an evaluation controlled outside the
adaptive runtime without placing the sealed corpus, oracle answers, generator,
or independent results inside this repository.

## Boundary

external corpus + task generator + oracle
-> isolated HWS submission
-> independent evaluator
-> attested campaign outcome
-> learning intervention
-> new sealed holdout
-> re-test
-> promotion or rollback evidence

HWS owns the protocol and lineage schema. The external evaluation owner retains
control of the corpus, generator, oracle, execution isolation, and attestation.

## Required campaign identity

ExternalEvaluationCampaign binds:
- corpus digest
- generator digest
- oracle digest
- arena/evaluator versions
- runtime snapshot
- evaluated case identities
- explicit holdout identities
- transfer dimensions

No task payloads or oracle answers belong in the campaign record.

## Required transfer dimensions

A credible external campaign should include as applicable:
- novel domains
- unfamiliar tools
- long-horizon tasks
- adversarial tasks
- multimodal tasks

The protocol makes coverage explicit without claiming every campaign must
contain every modality.

## Trust and learning gates

An outcome is evidence-eligible only as a bounded metric record. A campaign is
trustworthy only when contamination is false and external attestation exists.
Learning interventions are hypotheses, not promotions. Every intervention must
produce a CampaignRetestContract requiring a new independent holdout.

Promotion and rollback remain outside this protocol and should be governed by
the existing capability lifecycle.

## Independence rule

Do not commit a real sealed benchmark corpus, oracle answers, task generator
secrets, or independent campaign results to HWS. Repository tests validate the
protocol, not the independence of an external evaluation.

For production-strength independence, the external owner should run the arena
and evaluator in separate processes or containers, provide read-only benchmark
access, and sign or attest the final campaign receipt.

## What this does not establish

Passing repository tests or producing a campaign record does not establish
general intelligence. Credible progress requires repeated external holdouts,
generalization across domains and task forms, long-horizon success, robust
calibration, efficiency, and independently attested outcomes.
