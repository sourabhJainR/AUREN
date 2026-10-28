from __future__ import annotations

import unittest

from portable.provider_operation_lifecycle import stable_provider_operation_id


class ProviderOperationIdentityEncodingTests(unittest.TestCase):
    def test_operation_and_provider_delimiter_text_cannot_collide(self):
        intent = {"target": "staging", "revision": "release-42"}

        # These pairs produce the same bytes when fields are joined using the
        # literal "\\0" delimiter. Structured canonical encoding must keep
        # the operation key and provider identity unambiguous.
        first = stable_provider_operation_id(
            "tenant\\0deploy", "provider", intent,
        )
        second = stable_provider_operation_id(
            "tenant", "deploy\\0provider", intent,
        )

        self.assertNotEqual(first, second)
        self.assertEqual(
            first,
            stable_provider_operation_id("tenant\\0deploy", "provider", intent),
        )


if __name__ == "__main__":
    unittest.main()
