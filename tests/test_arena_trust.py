from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import unittest

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

from portable.arena_trust import Ed25519TrustRegistry, canonical_bytes


@unittest.skipUnless(CRYPTO_AVAILABLE, "cryptography Ed25519 support is required")
class Ed25519TrustRegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        self.registry = Ed25519TrustRegistry(
            public_keys={"evaluator-key-v1": public},
            validity={"evaluator-key-v1": {
                "not_before": (self.now - timedelta(minutes=5)).isoformat(),
                "not_after": (self.now + timedelta(days=1)).isoformat(),
            }},
        )

    def test_valid_signature_passes_and_tampering_fails(self):
        payload = {"schema_version": "auren-ed25519-trust-registry-v1", "kind": "campaign", "binding_digest": "a" * 64, "signer_id": "evaluator-key-v1", "issued_at": self.now.isoformat(), "payload": {"result": "pass"}}
        signature = base64.b64encode(self.private.sign(canonical_bytes(payload))).decode()
        self.assertTrue(self.registry.verify(signer_id="evaluator-key-v1", payload=payload, signature_b64=signature, now=self.now))
        changed = dict(payload, binding_digest="b" * 64)
        self.assertFalse(self.registry.verify(signer_id="evaluator-key-v1", payload=changed, signature_b64=signature, now=self.now))

    def test_revoked_expired_and_unknown_signers_fail_closed(self):
        payload = {"result": "pass"}
        signature = base64.b64encode(self.private.sign(canonical_bytes(payload))).decode()
        revoked = Ed25519TrustRegistry(
            public_keys=self.registry._keys,
            validity=self.registry._validity,
            revoked_signers={"evaluator-key-v1"},
        )
        self.assertFalse(revoked.verify(signer_id="evaluator-key-v1", payload=payload, signature_b64=signature, now=self.now))
        self.assertFalse(self.registry.verify(signer_id="unknown", payload=payload, signature_b64=signature, now=self.now))
        self.assertFalse(self.registry.verify(signer_id="evaluator-key-v1", payload=payload, signature_b64=signature, now=self.now + timedelta(days=2)))

    def test_role_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            Ed25519TrustRegistry(
                public_keys={"a": "00" * 32, "b": "11" * 32},
                validity={"a": {"not_before": self.now.isoformat(), "not_after": (self.now + timedelta(days=1)).isoformat()}, "b": {"not_before": self.now.isoformat(), "not_after": (self.now + timedelta(days=1)).isoformat()}},
                role_assignments={"operator": "a", "evaluator": "a"},
            )


if __name__ == "__main__":
    unittest.main()
