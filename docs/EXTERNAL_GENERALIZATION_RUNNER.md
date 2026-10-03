# External Generalization Campaign Runner

The runner turns the external-evaluation protocol into an executable boundary.
HWS supplies orchestration and metric calculation; the environment adapter and
independent oracle remain outside the adaptive runtime's authority.

The runner:
1. binds the environment contract to the campaign identity
2. requests each case from the external environment
3. verifies traces through an independently supplied oracle
4. rejects oracle/trace disagreement
5. evaluates holdout transfer, verification, horizon, modality and tool-use
6. emits a bounded CampaignOutcome

It does not:
- contain benchmark answers
- generate the external benchmark
- control the external environment
- mutate capability state
- promote or rollback capabilities

A production evaluator should isolate the environment and oracle in separate
processes or containers and attest their identities and outputs. Repository
tests use fakes only to validate protocol behavior.
