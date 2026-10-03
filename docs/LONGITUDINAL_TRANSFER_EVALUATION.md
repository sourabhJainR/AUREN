# Longitudinal Transfer Evaluation

Phase 27 adds an explicit evidence surface for longitudinal generalization.

LongitudinalTransferEvaluator filters observations to independently-oracled, non-contaminated outcomes and measures:

- number of genuinely novel domains;
- novel-domain pass rate;
- fresh holdout pass rate;
- transfer gap;
- deterministic profile digest.

A profile is trustworthy only when it contains both novel and holdout evidence and spans at least two novel domains.

This is an evaluation artifact, not an AGI claim or promotion mechanism. It makes progress measurable across time and domain shifts rather than relying only on aggregate internal benchmark scores.
