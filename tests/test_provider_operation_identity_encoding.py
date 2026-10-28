from __future__ import annotations

import unittest

from portable.provider_operation_lifecycle import stable_provider_operation_id


class ProviderOperationIdentityEncodingTests(unittest.TestCase):
    def test_reserved_delimiter_is_rejected_without_changing_legacy_operation_ids(self):
        intent = {"target": "staging", "revision": "release-42"}

        # Existing durable operation IDs must remain stable across upgrades.
        # Reserve the legacy delimiter rather than changing the hash format.
        expected = stable_provider_operation_id("tenant-deploy", "provider", intent)
        self.assertEqual(
            expected,
            stable_provider_operation_id("tenant-deploy", "provider", intent),
        )

        with self.assertRaisesRegex(ValueError, "reserved"):
            stable_provider_operation_id("tenant\\0deploy", "provider", intent)
        with self.assertRaisesRegex(ValueError, "reserved"):
            stable_provider_operation_id("tenant", "deploy\\0provider", intent)


if __name__ == "__main__":
    unittest.main()
