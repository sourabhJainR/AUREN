import unittest

from portable.benchmark_manifest import BenchmarkManifest


class BenchmarkManifestTests(unittest.TestCase):
    def _manifest(self):
        return BenchmarkManifest.seal(
            "agi-engineering", "1",
            train_domains=("coding", "research"),
            holdout_domains=("planning", "reasoning"),
            oracle_ids=("oracle-v1",),
        )

    def test_seal_is_deterministic(self):
        a = self._manifest()
        b = BenchmarkManifest.seal(
            "agi-engineering", "1",
            train_domains=("research", "coding"),
            holdout_domains=("reasoning", "planning"),
            oracle_ids=("oracle-v1",),
        )
        self.assertEqual(a.digest, b.digest)

    def test_accepts_only_manifest_bound_holdout(self):
        manifest = self._manifest()
        ok, reason = manifest.validate(
            domain="reasoning", holdout=True,
            oracle_id="oracle-v1", manifest_digest=manifest.digest,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "manifest validation passed")

    def test_rejects_split_or_oracle_tampering(self):
        manifest = self._manifest()
        checks = (
            manifest.validate(domain="coding", holdout=True,
                              oracle_id="oracle-v1", manifest_digest=manifest.digest),
            manifest.validate(domain="reasoning", holdout=True,
                              oracle_id="oracle-v2", manifest_digest=manifest.digest),
            manifest.validate(domain="reasoning", holdout=True,
                              oracle_id="oracle-v1", manifest_digest="tampered"),
        )
        self.assertTrue(all(not ok for ok, _ in checks))

    def test_rejects_overlapping_splits(self):
        with self.assertRaises(ValueError):
            BenchmarkManifest.seal(
                "x", "1",
                train_domains=("coding",),
                holdout_domains=("coding",),
                oracle_ids=("oracle-v1",),
            )


if __name__ == "__main__":
    unittest.main()
