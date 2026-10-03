"""Sealed benchmark manifests for evaluation integrity.

A manifest binds benchmark identity, domain splits, and oracle identities to a
canonical digest. It does not execute tasks or grant execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable


@dataclass(frozen=True)
class BenchmarkManifest:
    benchmark_id: str
    version: str
    train_domains: tuple[str, ...]
    holdout_domains: tuple[str, ...]
    oracle_ids: tuple[str, ...]
    digest: str

    @staticmethod
    def seal(
        benchmark_id: str,
        version: str,
        *,
        train_domains: Iterable[str],
        holdout_domains: Iterable[str],
        oracle_ids: Iterable[str],
    ) -> "BenchmarkManifest":
        train = tuple(sorted({str(x).strip() for x in train_domains if str(x).strip()}))
        holdout = tuple(sorted({str(x).strip() for x in holdout_domains if str(x).strip()}))
        oracles = tuple(sorted({str(x).strip() for x in oracle_ids if str(x).strip()}))
        if not benchmark_id.strip() or not version.strip():
            raise ValueError("benchmark_id and version are required")
        if not holdout:
            raise ValueError("at least one holdout domain is required")
        if set(train) & set(holdout):
            raise ValueError("train and holdout domains must be disjoint")
        if not oracles:
            raise ValueError("at least one oracle id is required")
        payload = {
            "benchmark_id": benchmark_id.strip(),
            "version": version.strip(),
            "train_domains": train,
            "holdout_domains": holdout,
            "oracle_ids": oracles,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return BenchmarkManifest(
            payload["benchmark_id"],
            payload["version"],
            train,
            holdout,
            oracles,
            digest,
        )

    def validate(
        self,
        *,
        domain: str,
        holdout: bool,
        oracle_id: str,
        manifest_digest: str,
    ) -> tuple[bool, str]:
        if manifest_digest != self.digest:
            return False, "benchmark manifest digest mismatch"
        if oracle_id not in self.oracle_ids:
            return False, "oracle is not registered in benchmark manifest"
        domain = str(domain).strip()
        if holdout and domain not in self.holdout_domains:
            return False, "case is marked holdout outside the sealed holdout split"
        if not holdout and domain not in self.train_domains:
            return False, "non-holdout case is outside the sealed training split"
        return True, "manifest validation passed"


__all__ = ["BenchmarkManifest"]
