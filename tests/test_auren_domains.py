from __future__ import annotations

import unittest

from portable.auren_domains import DOMAINS, canonical_domain_keys, domain_for


class AurenDomainTests(unittest.TestCase):
    def test_six_domains_are_unique_and_canonical(self) -> None:
        self.assertEqual(canonical_domain_keys(), ("core", "engine", "memory", "learning", "arena", "guard"))
        self.assertEqual(len({domain.name for domain in DOMAINS}), 6)

    def test_unknown_domain_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            domain_for("unknown")

    def test_learning_and_arena_have_no_authority(self) -> None:
        self.assertIn("cannot grant permissions", domain_for("learning").authority)
        self.assertIn("cannot promote", domain_for("arena").authority)

    def test_guard_owns_consequential_authority(self) -> None:
        self.assertIn("authorizes consequential", domain_for("guard").authority)


if __name__ == "__main__":
    unittest.main()
