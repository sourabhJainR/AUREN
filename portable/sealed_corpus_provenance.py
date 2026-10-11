"""Corpus provenance primitives for sealed Arena evaluation.

Provenance claims are content-addressed and must be covered by an externally
verified receipt signature before they are trusted by an independent gate.
This module intentionally does not treat a self-asserted boolean as proof.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from urllib.parse import urlparse


def _text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class SealedCorpusProvenance:
    """Signed provenance metadata bound to one immutable corpus manifest."""

    corpus_digest: str
    manifest_digest: str
    origin: str
    split: str
    contamination_status: str
    independent_operator_id: str
    attestation_digest: str

    def __post_init__(self) -> None:
        for name, value in (
            ("corpus_digest", self.corpus_digest),
            ("manifest_digest", self.manifest_digest),
            ("origin", self.origin),
            ("split", self.split),
            ("contamination_status", self.contamination_status),
            ("independent_operator_id", self.independent_operator_id),
            ("attestation_digest", self.attestation_digest),
        ):
            _text(name, value)
        parsed = urlparse(self.origin)
        if parsed.scheme not in {"https", "file", "urn"}:
            raise ValueError("origin must use an explicit https, file, or urn URI")
        if self.split not in {"development", "evaluation", "sealed_holdout"}:
            raise ValueError("split must be development, evaluation, or sealed_holdout")
        if self.contamination_status not in {"clean", "contaminated", "unknown"}:
            raise ValueError("contamination_status must be clean, contaminated, or unknown")

    @property
    def provenance_digest(self) -> str:
        canonical = json.dumps(
            {
                "corpus_digest": self.corpus_digest,
                "manifest_digest": self.manifest_digest,
                "origin": self.origin,
                "split": self.split,
                "contamination_status": self.contamination_status,
                "independent_operator_id": self.independent_operator_id,
                "attestation_digest": self.attestation_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def validate_for_independent_gate(
        self,
        *,
        expected_corpus_digest: str,
        expected_manifest_digest: str,
        evaluator_id: str,
        oracle_id: str,
    ) -> None:
        """Fail closed unless provenance is bound to this corpus and split."""
        if self.corpus_digest != expected_corpus_digest:
            raise ValueError("corpus provenance digest does not match request corpus")
        if self.manifest_digest != expected_manifest_digest:
            raise ValueError("corpus provenance manifest does not match sealed manifest")
        if self.split != "sealed_holdout":
            raise ValueError("independent gate requires sealed_holdout provenance")
        if self.contamination_status != "clean":
            raise ValueError("independent gate rejects contaminated or unknown provenance")
        if self.independent_operator_id in {evaluator_id, oracle_id}:
            raise ValueError("corpus provenance operator must be independent of evaluator and oracle")


__all__ = ["SealedCorpusProvenance"]
