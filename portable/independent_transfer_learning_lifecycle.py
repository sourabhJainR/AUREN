"""Independent benchmark -> transfer -> causal learning lifecycle.

This module composes existing AUREN evaluation primitives into one fail-closed
protocol. Benchmark generation and oracle ownership remain external to runtime
execution. Promotion is a versioned policy artifact only; it never grants
permissions or execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Mapping, Sequence

from .benchmark_manifest import BenchmarkManifest
from .sealed_arena_boundary import SealedCaseEnvelope, SealedCampaignRequest
from .causal_capability_promotion import (
    CausalCapabilityPromotionGate,
    CausalPromotionDecision,
    CausalPromotionEvidence,
)
from .external_evaluation_campaign import CampaignRetestContract
from .arena_run_receipt import ArenaRunReceipt


@dataclass(frozen=True, slots=True)
class GeneratedBenchmark:
    benchmark_id: str
    version: str
    generator_id: str
    generator_digest: str
    manifest: BenchmarkManifest
    request: SealedCampaignRequest

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps({
            "benchmark_id": self.benchmark_id,
            "version": self.version,
            "generator_id": self.generator_id,
            "generator_digest": self.generator_digest,
            "manifest": self.manifest.digest,
            "request": self.request.campaign_digest,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class IndependentBenchmarkGenerator:
    """Create opaque cases from an independently supplied task specification.

    The generator receives task/input digests, never answers. A generator
    principal must differ from both the runtime and oracle principals.
    """

    def generate(
        self,
        *,
        benchmark_id: str,
        version: str,
        generator_id: str,
        runtime_id: str,
        oracle_ids: Iterable[str],
        corpus_digest: str,
        domains: Mapping[str, Sequence[tuple[str, str, bool]]],
        holdout_domains: Iterable[str],
    ) -> GeneratedBenchmark:
        if not generator_id.strip() or generator_id == runtime_id:
            raise ValueError("benchmark generator must be independent of runtime")
        oracle_ids = tuple(sorted({str(x).strip() for x in oracle_ids if str(x).strip()}))
        if generator_id in oracle_ids:
            raise ValueError("benchmark generator must be independent of oracle")
        holdouts = tuple(sorted({str(x).strip() for x in holdout_domains if str(x).strip()}))
        train_domains = tuple(sorted(set(domains) - set(holdouts)))
        if not train_domains or not holdouts:
            raise ValueError("benchmark needs disjoint train and holdout domains")
        if not set(holdouts) <= set(domains):
            raise ValueError("holdout domains must exist in generated benchmark")
        manifest = BenchmarkManifest.seal(
            benchmark_id, version,
            train_domains=train_domains,
            holdout_domains=holdouts,
            oracle_ids=oracle_ids,
        )
        cases = []
        for domain in sorted(domains):
            for case_id, task_digest, holdout in domains[domain]:
                if not case_id.strip() or not task_digest.strip():
                    raise ValueError("case id and task digest are required")
                if bool(holdout) != (domain in holdouts):
                    raise ValueError("case holdout flag must match sealed domain split")
                cases.append(SealedCaseEnvelope(
                    case_id=case_id,
                    task_digest=task_digest,
                    input_digest=hashlib.sha256(
                        f"{benchmark_id}:{version}:{case_id}".encode()
                    ).hexdigest(),
                    environment_digest=hashlib.sha256(
                        f"{domain}:{version}".encode()
                    ).hexdigest(),
                    holdout=bool(holdout),
                    transfer_dimensions=("cross_domain", domain),
                ))
        if len({c.case_id for c in cases}) != len(cases):
            raise ValueError("benchmark contains duplicate case ids")
        campaign_digest = hashlib.sha256(json.dumps({
            "benchmark": benchmark_id, "version": version,
            "manifest": manifest.digest,
            "corpus": corpus_digest,
            "generator": generator_id,
            "cases": [c.as_dict() for c in cases],
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        request = SealedCampaignRequest(campaign_digest, corpus_digest, tuple(cases))
        generator_digest = hashlib.sha256(json.dumps(
            [c.as_dict() for c in cases], sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        return GeneratedBenchmark(
            benchmark_id, version, generator_id, generator_digest, manifest, request
        )


@dataclass(frozen=True, slots=True)
class DomainTransferMeasurement:
    source_domain: str
    target_domains: tuple[str, ...]
    source_baseline: float
    target_baseline: float
    target_treatment: float
    transfer_lift: float
    regression_count: int
    sample_count: int
    valid: bool

    def __post_init__(self) -> None:
        for name in ("source_baseline", "target_baseline", "target_treatment"):
            if not 0.0 <= float(getattr(self, name)) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.sample_count < 1 or self.regression_count < 0:
            raise ValueError("invalid transfer counts")


class CrossDomainTransferMeasurer:
    """Measure transfer from one source domain to independently held-out targets."""

    def measure(
        self,
        *,
        source_domain: str,
        outcomes: Sequence[tuple[str, str, bool, float, bool, bool]],
        minimum_target_domains: int = 2,
        minimum_lift: float = 0.03,
    ) -> DomainTransferMeasurement:
        # (domain, cohort, success, score, regression, holdout). Target cohorts must be sealed holdouts.
        if not source_domain.strip():
            raise ValueError("source_domain is required")
        target_domains = sorted({
            domain for domain, cohort, _success, _score, _regression, holdout in outcomes
            if holdout and domain != source_domain and cohort == "treatment"
        })
        if len(target_domains) < minimum_target_domains:
            raise ValueError("insufficient independent target domains")
        source = [score for domain, cohort, _s, score, _r, holdout in outcomes
                  if domain == source_domain and cohort == "control" and not holdout]
        target_control = [score for domain, cohort, _s, score, _r, holdout in outcomes
                          if domain != source_domain and cohort == "control" and holdout]
        target_treatment = [score for domain, cohort, _s, score, _r, holdout in outcomes
                            if domain != source_domain and cohort == "treatment" and holdout]
        regressions = sum(bool(r) for domain, _c, _s, _score, r, holdout in outcomes if domain != source_domain and holdout)
        if not source or not target_control or not target_treatment:
            raise ValueError("source and target control/treatment observations are required")
        source_baseline = sum(source) / len(source)
        target_baseline = sum(target_control) / len(target_control)
        treatment = sum(target_treatment) / len(target_treatment)
        lift = treatment - target_baseline
        return DomainTransferMeasurement(
            source_domain, tuple(target_domains), source_baseline, target_baseline,
            treatment, lift, regressions, len(outcomes),
            lift >= minimum_lift and regressions == 0,
        )


@dataclass(frozen=True, slots=True)
class CausalAttribution:
    control_mean: float
    treatment_mean: float
    treatment_effect: float
    standard_error: float
    confidence: float
    randomized: bool
    independent_holdout: bool
    attributable: bool


class CausalAttributionEstimator:
    """Estimate bounded treatment effect from randomized evidence."""

    @staticmethod
    def estimate(control_scores: Sequence[float], treatment_scores: Sequence[float], *, randomized: bool, independent_holdout: bool) -> CausalAttribution:
        if not control_scores or not treatment_scores:
            raise ValueError("control and treatment observations are required")
        values = tuple(control_scores) + tuple(treatment_scores)
        if any(not 0.0 <= float(x) <= 1.0 for x in values):
            raise ValueError("scores must be between 0 and 1")
        control_mean = sum(control_scores) / len(control_scores)
        treatment_mean = sum(treatment_scores) / len(treatment_scores)
        effect = treatment_mean - control_mean
        def variance(values, mean_value):
            return sum((float(x) - mean_value) ** 2 for x in values) / max(1, len(values) - 1)
        standard_error = (variance(control_scores, control_mean) / len(control_scores) + variance(treatment_scores, treatment_mean) / len(treatment_scores)) ** 0.5
        signal = min(1.0, abs(effect) / max(standard_error, 0.02))
        sample_confidence = min(1.0, (len(control_scores) + len(treatment_scores)) / 20.0)
        confidence = round(signal * sample_confidence, 4)
        return CausalAttribution(round(control_mean, 4), round(treatment_mean, 4), round(effect, 4), round(standard_error, 4), confidence, randomized, independent_holdout, randomized and independent_holdout and effect > 0.0)


@dataclass(frozen=True, slots=True)
class LifecyclePromotionRecord:
    capability_id: str
    version: str
    state: str
    evidence_digest: str
    prior_version: str = ""

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps({"capability_id": self.capability_id, "version": self.version, "state": self.state, "evidence_digest": self.evidence_digest, "prior_version": self.prior_version}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PromotionRollbackLedger:
    """Versioned promotion state; never changes execution permissions."""

    def __init__(self) -> None:
        self._active: dict[str, LifecyclePromotionRecord] = {}
        self._history: dict[str, list[LifecyclePromotionRecord]] = {}

    def promote(self, capability_id: str, version: str, evidence_digest: str) -> LifecyclePromotionRecord:
        if not capability_id.strip() or not version.strip() or not evidence_digest.strip():
            raise ValueError("capability, version, and evidence are required")
        prior = self._active.get(capability_id)
        record = LifecyclePromotionRecord(
            capability_id, version, "promoted", evidence_digest,
            prior.version if prior else "",
        )
        self._history.setdefault(capability_id, []).append(record)
        self._active[capability_id] = record
        return record

    def rollback(self, capability_id: str, *, reason: str) -> LifecyclePromotionRecord:
        prior = self._active.get(capability_id)
        if prior is None:
            raise KeyError("no promoted capability exists")
        candidates = [
            x for x in self._history.get(capability_id, [])
            if x.version == prior.prior_version
        ]
        if not candidates:
            raise ValueError("no prior promoted version is available for rollback")
        restored = candidates[-1]
        record = LifecyclePromotionRecord(
            capability_id, restored.version, "rolled_back", reason, prior.version
        )
        self._history.setdefault(capability_id, []).append(record)
        self._active[capability_id] = restored
        return record

    def active(self, capability_id: str) -> LifecyclePromotionRecord | None:
        return self._active.get(capability_id)


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    benchmark: GeneratedBenchmark
    transfer: DomainTransferMeasurement
    causal: CausalPromotionDecision
    retest: CampaignRetestContract
    promotion: LifecyclePromotionRecord | None
    rolled_back: bool


class IndependentTransferLearningLifecycle:
    """Bind generation, sealed evidence, transfer, attribution, retest and policy state."""

    def __init__(
        self,
        *,
        promotion_ledger: PromotionRollbackLedger | None = None,
        causal_gate: CausalCapabilityPromotionGate | None = None,
    ) -> None:
        self.promotions = promotion_ledger or PromotionRollbackLedger()
        self.causal_gate = causal_gate or CausalCapabilityPromotionGate()

    def evaluate(
        self,
        *,
        benchmark: GeneratedBenchmark,
        capability_id: str,
        intervention_id: str,
        baseline_score: float,
        control_score: float,
        treatment_score: float,
        holdout_score: float,
        attribution_confidence: float,
        transfer: DomainTransferMeasurement,
        fresh_holdout: bool,
        independent_oracle: bool,
        evidence_ids: Iterable[str],
        prior_campaign_digest: str,
        run_receipt: ArenaRunReceipt | None = None,
        randomized_assignment: bool = False,
        control_scores: Sequence[float] | None = None,
        treatment_scores: Sequence[float] | None = None,
        version: str = "candidate",
    ) -> LifecycleResult:
        if run_receipt is None:
            raise ValueError("promotion evaluation requires a sealed execution receipt")
        if run_receipt.corpus_digest != benchmark.request.corpus_digest:
            raise ValueError("run receipt corpus does not match benchmark")
        if run_receipt.manifest_digest != benchmark.manifest.digest:
            raise ValueError("run receipt manifest does not match benchmark")
        sealed_holdouts = {case.case_id for case in benchmark.request.cases if case.holdout}
        if not set(run_receipt.holdout_case_ids) <= sealed_holdouts:
            raise ValueError("run receipt contains an unsealed holdout case")
        if not run_receipt.trustworthy:
            raise ValueError("run receipt is not trustworthy")
        if not randomized_assignment:
            raise ValueError("causal attribution requires randomized treatment assignment")
        if control_scores is not None or treatment_scores is not None:
            if control_scores is None or treatment_scores is None:
                raise ValueError("control and treatment score samples must be supplied together")
            attribution = CausalAttributionEstimator.estimate(
                control_scores, treatment_scores,
                randomized=True, independent_holdout=fresh_holdout,
            )
            attribution_confidence = attribution.confidence
            control_score = attribution.control_mean
            treatment_score = attribution.treatment_mean
        evidence = CausalPromotionEvidence(
            capability_id, intervention_id, baseline_score, control_score,
            treatment_score, holdout_score, attribution_confidence,
            fresh_holdout, independent_oracle, False,
            tuple(dict.fromkeys(str(x) for x in evidence_ids if str(x))),
        )
        decision = self.causal_gate.evaluate(evidence)
        retest = CampaignRetestContract(
            prior_campaign_digest=prior_campaign_digest,
            intervention_digest=intervention_id,
        )
        promotion = None
        rolled_back = False
        if decision.eligible and transfer.valid:
            promotion = self.promotions.promote(
                capability_id, version, evidence.evidence_digest
            )
        elif self.promotions.active(capability_id) is not None:
            self.promotions.rollback(
                capability_id,
                reason="causal attribution, transfer, or holdout gate failed",
            )
            rolled_back = True
        return LifecycleResult(
            benchmark, transfer, decision, retest, promotion, rolled_back
        )


__all__ = [
    "GeneratedBenchmark", "IndependentBenchmarkGenerator", "CausalAttribution",
    "CausalAttributionEstimator",
    "DomainTransferMeasurement", "CrossDomainTransferMeasurer",
    "LifecyclePromotionRecord", "PromotionRollbackLedger",
    "LifecycleResult", "IndependentTransferLearningLifecycle",
]
