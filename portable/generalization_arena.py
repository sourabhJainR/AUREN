"""Independent generalization arena boundary.

The arena deliberately keeps benchmark corpus and oracle ownership outside the
adaptive runtime. The runtime may submit an execution request, but it cannot
select or mutate the sealed corpus or oracle registry.

This module is protocol/evaluation infrastructure only; it does not grant
execution authority and does not claim AGI.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .benchmark_manifest import BenchmarkManifest
from .general_intelligence_evaluation import ExecutionCase, GeneralIntelligenceEvaluation, ExecutionBackedEvaluator


@dataclass(frozen=True)
class ArenaCase:
    case_id: str
    kind: str
    domain: str
    holdout: bool
    task: Mapping[str, Any]
    oracle_id: str


@dataclass(frozen=True)
class ArenaCorpus:
    manifest: BenchmarkManifest
    cases: tuple[ArenaCase, ...]
    corpus_digest: str


class IndependentGeneralizationArena:
    """Load a sealed corpus from an external root and evaluate submitted runners."""

    def __init__(
        self,
        *,
        corpus_root: str | Path,
        runtime_root: str | Path,
        oracles: Mapping[str, Callable[[Any], bool]],
        evaluator: ExecutionBackedEvaluator | None = None,
    ) -> None:
        self.corpus_root = Path(corpus_root).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        if (self.corpus_root == self.runtime_root or self.runtime_root in self.corpus_root.parents or self.corpus_root in self.runtime_root.parents):
            raise ValueError("benchmark corpus must be outside the adaptive runtime root")
        self.oracles = dict(oracles)
        self.evaluator = evaluator or ExecutionBackedEvaluator()

    def load(self) -> ArenaCorpus:
        manifest_path = self.corpus_root / "manifest.json"
        corpus_path = self.corpus_root / "cases.json"
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        cases_data = json.loads(corpus_path.read_text(encoding="utf-8"))
        if not isinstance(manifest_data, dict):
            raise ValueError("sealed benchmark manifest must be a JSON object")
        for field in ("train_domains", "holdout_domains", "oracle_ids"):
            values = manifest_data.get(field)
            if not isinstance(values, list) or not values or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError(f"manifest {field} must be a non-empty array of strings")
        for field in ("benchmark_id", "version", "digest"):
            if not isinstance(manifest_data.get(field), str) or not manifest_data[field].strip():
                raise ValueError(f"manifest {field} must be a non-empty string")
        manifest = BenchmarkManifest.seal(
            manifest_data["benchmark_id"],
            manifest_data["version"],
            train_domains=manifest_data["train_domains"],
            holdout_domains=manifest_data["holdout_domains"],
            oracle_ids=manifest_data["oracle_ids"],
        )
        if (
            manifest.digest != manifest_data.get("digest")
            or list(manifest.train_domains) != manifest_data["train_domains"]
            or list(manifest.holdout_domains) != manifest_data["holdout_domains"]
            or list(manifest.oracle_ids) != manifest_data["oracle_ids"]
        ):
            raise ValueError("sealed benchmark manifest digest or canonical ordering mismatch")
        if not isinstance(cases_data, list) or not cases_data:
            raise ValueError("sealed corpus must contain cases")
        cases_list = []
        for index, row in enumerate(cases_data):
            if not isinstance(row, dict):
                raise ValueError(f"sealed case at index {index} must be a JSON object")
            for field in ("case_id", "kind", "domain", "oracle_id"):
                if not isinstance(row.get(field), str) or not row[field].strip():
                    raise ValueError(f"sealed case at index {index} has invalid {field}")
            if type(row.get("holdout")) is not bool:
                raise ValueError(f"sealed case {row['case_id']} holdout must be a boolean")
            if not isinstance(row.get("task"), dict):
                raise ValueError(f"sealed case {row['case_id']} task must be a JSON object")
            cases_list.append(ArenaCase(
                case_id=row["case_id"],
                kind=row["kind"],
                domain=row["domain"],
                holdout=row["holdout"],
                task=dict(row["task"]),
                oracle_id=row["oracle_id"],
            ))
        cases = tuple(cases_list)
        ids = [case.case_id for case in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("sealed corpus contains duplicate case ids")
        for case in cases:
            ok, reason = manifest.validate(
                domain=case.domain,
                holdout=case.holdout,
                oracle_id=case.oracle_id,
                manifest_digest=manifest.digest,
            )
            if not ok:
                raise ValueError(f"invalid sealed case {case.case_id}: {reason}")
            if case.oracle_id not in self.oracles:
                raise ValueError(f"missing independent oracle: {case.oracle_id}")
        canonical = json.dumps(
            {"manifest": manifest.digest, "cases": cases_data},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        return ArenaCorpus(manifest, cases, digest)

    def evaluate(
        self,
        *,
        runners: Mapping[str, Callable[[Mapping[str, Any]], Any]],
    ) -> GeneralIntelligenceEvaluation:
        corpus = self.load()
        cases = []
        for case in corpus.cases:
            runner = runners.get(case.case_id)
            oracle = self.oracles[case.oracle_id]
            if runner is None:
                def missing_runner(_task: Mapping[str, Any], cid: str = case.case_id) -> Any:
                    raise ValueError(f"no runner submitted for sealed case {cid}")
                runner = missing_runner
            cases.append(
                ExecutionCase(
                    case_id=case.case_id,
                    kind=case.kind,
                    domain=case.domain,
                    holdout=case.holdout,
                    runner=lambda runner=runner, task=case.task: runner(task),
                    oracle=oracle,
                    evidence_factory=lambda observed, cid=case.case_id: (
                        f"arena:{corpus.corpus_digest}:{cid}:{hashlib.sha256(repr(observed).encode()).hexdigest()}",
                    ),
                    manifest=corpus.manifest,
                    oracle_id=case.oracle_id,
                    manifest_digest=corpus.manifest.digest,
                )
            )
        report = self.evaluator.evaluate(cases)
        # Detect persistent corpus or manifest mutation during the evaluation window.
        # This is an integrity recheck, not proof against a privileged operator that
        # mutates and restores files between reads; production custody stays external.
        after = self.load()
        if after.corpus_digest != corpus.corpus_digest:
            raise ValueError("sealed benchmark corpus changed during evaluation")
        return report


__all__ = ["ArenaCase", "ArenaCorpus", "IndependentGeneralizationArena"]
