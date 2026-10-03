# Causal Capability Promotion Gate

This module closes an important learning-loop weakness: a post-intervention
score increase is not sufficient evidence that the intervention caused the
improvement.

The gate requires:
- treatment performance to exceed a control group by a configured lift
- a fresh holdout to improve over the baseline
- independent oracle evidence
- explicit attribution confidence
- no known contamination
- unique evidence identifiers
- no control-group regression beyond tolerance

The gate is deliberately decision-only. It does not mutate the capability
lifecycle or grant execution authority.

## AGI-evaluation relevance

This is stronger evidence for capability acquisition because it separates
learning correlation from intervention attribution. The next external campaign
can use this record together with ExternalEvaluationCampaign and ArenaRunReceipt
to support a reproducible chain:

external evaluation -> intervention -> controlled retest -> fresh holdout ->
causal attribution -> lifecycle decision

It still does not establish general intelligence. Independent external
evaluation across broad domains and modalities remains necessary.
