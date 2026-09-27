from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest

from portable.engineering_evidence_envelope import EngineeringEvidenceEnvelope, EvidenceRef
from portable.evidence_contract import EvidenceClaim, EvidenceSpine


class EngineeringEvidenceEnvelopeTests(unittest.TestCase):
    def _envelope(self) -> EngineeringEvidenceEnvelope:
        return EngineeringEvidenceEnvelope(
            task_id="T-1",
            intent_digest="intent-1",
            repository_snapshot_digest="repo-1",
            context_plan_digest="plan-1",
            evidence=(EvidenceRef("e1", "repo-1", "current"), EvidenceRef("e2", "repo-1")),
        )

    def test_digest_is_content_addressed_and_serializable(self) -> None:
        envelope = self._envelope()
        restored = EngineeringEvidenceEnvelope.from_state(envelope.to_state())
        self.assertEqual(restored.envelope_digest, envelope.envelope_digest)
        self.assertEqual(restored.as_dict(), envelope.as_dict())

    def test_evidence_reference_snapshot_is_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            EngineeringEvidenceEnvelope(
                task_id="T-1",
                intent_digest="intent-1",
                repository_snapshot_digest="repo-1",
                evidence=(EvidenceRef("e1", "repo-2"),),
            )

    def test_bind_creates_immutable_lineage(self) -> None:
        first = self._envelope()
        second = first.bind(changeset_id="change-1", verification_ids=("verify-1",))
        self.assertEqual(first.changeset_id, "")
        self.assertEqual(first.verification_ids, ())
        self.assertEqual(second.parent_envelope_id, first.envelope_digest)
        self.assertNotEqual(second.envelope_digest, first.envelope_digest)
        self.assertEqual(second.changeset_id, "change-1")

    def test_context_evidence_becomes_envelope_without_copying_claims(self) -> None:
        context = SimpleNamespace(
            task_id="T-2",
            intent_digest="intent-2",
            context_plan_digest="plan-2",
            repository_snapshot_digest="repo-2",
            evidence_digest="context-digest",
            items=(
                SimpleNamespace(evidence_id="code:one", freshness="1.0", text="fact"),
                SimpleNamespace(evidence_id="graph:two", freshness="1.0", text="graph"),
            ),
        )
        envelope = EngineeringEvidenceEnvelope.from_context_evidence(context)
        self.assertEqual(tuple(x.evidence_id for x in envelope.evidence), ("code:one", "graph:two"))
        self.assertEqual(dict(envelope.metadata)["context_evidence_digest"], "context-digest")
        self.assertNotIn("fact", str(envelope.as_dict()))

    def test_tampered_digest_is_rejected(self) -> None:
        envelope = self._envelope()
        tampered = replace(envelope, envelope_digest="tampered")
        with self.assertRaises(ValueError):
            EngineeringEvidenceEnvelope.from_state(tampered.to_state())

    def test_duplicate_stage_ids_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._envelope().bind(verification_ids=("verify-1", "verify-1"))

    def test_references_validate_against_canonical_evidence_spine(self) -> None:
        envelope = self._envelope()
        spine = EvidenceSpine((
            EvidenceClaim("e1", "source", "repo", "fact", "high", snapshot="repo-1", provenance="test"),
            EvidenceClaim("e2", "test", "ci", "test passed", "high", snapshot="repo-1", provenance="test"),
        ))
        envelope.validate_against(spine)
        with self.assertRaises(ValueError):
            EngineeringEvidenceEnvelope(
                task_id="T-1", intent_digest="intent-1", repository_snapshot_digest="repo-1",
                evidence=(EvidenceRef("missing", "repo-1"),),
            ).validate_against(spine)


if __name__ == "__main__":
    unittest.main()
