# Arena Run Receipts

The Independent Generalization Arena now has a content-addressed run receipt.

The receipt binds:

- sealed corpus and manifest digests;
- independently supplied oracle registry identity;
- evaluated case and holdout case IDs;
- passed and independently verified cases;
- runtime snapshot and evaluator version;
- duration and resource measurements;
- contamination status;
- immutable receipt digest.

The receipt is evidence lineage, not proof of AGI. It is deliberately not a
promotion mechanism and does not give the adaptive runtime access to benchmark
contents or evaluator control.

For credible external evaluation, the evaluator should retain the receipt
outside the adaptive runtime and, where available, add an independently managed
signature or attestation over the receipt digest.
