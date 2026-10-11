from __future__ import annotations

import unittest

from portable.sealed_corpus_provenance import SealedCorpusProvenance


class SealedCorpusProvenanceTests(unittest.TestCase):
    def provenance(self, **changes):
        values = {
            "corpus_digest": "corpus-sha256",
            "manifest_digest": "manifest-sha256",
            "origin": "https://benchmarks.example.org/holdout/v1",
            "split": "sealed_holdout",
            "contamination_status": "clean",
            "independent_operator_id": "operator-7",
            "attestation_digest": "attestation-sha256",
        }
        values.update(changes)
        return SealedCorpusProvenance(**values)

    def validate(self, provenance):
        provenance.validate_for_independent_gate(
            expected_corpus_digest="corpus-sha256",
            expected_manifest_digest="manifest-sha256",
            evaluator_id="evaluator-1",
            oracle_id="oracle-1",
        )

    def test_accepts_clean_corpus_bound_to_manifest_and_independent_operator(self):
        self.validate(self.provenance())

    def test_provenance_digest_is_stable_and_content_addressed(self):
        first = self.provenance()
        self.assertEqual(first.provenance_digest, self.provenance().provenance_digest)
        self.assertNotEqual(
            first.provenance_digest,
            self.provenance(origin="https://benchmarks.example.org/holdout/v2").provenance_digest,
        )

    def test_rejects_corpus_or_manifest_mismatch(self):
        with self.assertRaisesRegex(ValueError, "corpus provenance digest"):
            self.validate(self.provenance(corpus_digest="other"))
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.validate(self.provenance(manifest_digest="other"))

    def test_rejects_non_holdout_split(self):
        with self.assertRaisesRegex(ValueError, "sealed_holdout"):
            self.validate(self.provenance(split="development"))

    def test_rejects_unknown_or_contaminated_provenance(self):
        for status in ("unknown", "contaminated"):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "rejects contaminated"):
                self.validate(self.provenance(contamination_status=status))

    def test_rejects_operator_collusion_with_evaluator_or_oracle(self):
        for operator in ("evaluator-1", "oracle-1"):
            with self.subTest(operator=operator), self.assertRaisesRegex(ValueError, "independent"):
                self.validate(self.provenance(independent_operator_id=operator))

    def test_rejects_unqualified_origin(self):
        for origin in ("benchmark", "http://insecure.example.org/corpus"):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "explicit"):
                self.provenance(origin=origin)

    def test_rejects_missing_attestation_digest(self):
        with self.assertRaisesRegex(ValueError, "attestation_digest"):
            self.provenance(attestation_digest="")


if __name__ == "__main__":
    unittest.main()
