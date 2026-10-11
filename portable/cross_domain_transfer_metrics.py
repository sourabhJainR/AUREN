"""Evidence-backed metrics for cross-domain generalization campaigns."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .sealed_arena_boundary import SealedArenaEvidence
from .sealed_cross_domain_arena import CrossDomainCampaign


@dataclass(frozen=True, slots=True)
class TransferAggregate:
    cases: int
    passed: int
    pass_rate: float | None
    mean_uncertainty: float | None
    mean_calibration_error: float | None
    mean_resource_cost_ms: float | None
    recovery_rate: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "cases": self.cases,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "mean_uncertainty": self.mean_uncertainty,
            "mean_calibration_error": self.mean_calibration_error,
            "mean_resource_cost_ms": self.mean_resource_cost_ms,
            "recovery_rate": self.recovery_rate,
        }


@dataclass(frozen=True, slots=True)
class DomainTransferMetrics:
    domain_id: str
    novel: bool
    evaluation_mode: str
    metrics: TransferAggregate

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "novel": self.novel,
            "evaluation_mode": self.evaluation_mode,
            **self.metrics.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class CrossDomainTransferReport:
    campaign_id: str
    capability_id: str
    campaign_digest: str
    request_digest: str
    receipt_digest: str
    source_regression: TransferAggregate
    target_transfer: TransferAggregate
    by_domain: tuple[DomainTransferMetrics, ...]
    by_mode: dict[str, TransferAggregate]
    baseline_comparison_available: bool = False
    limitations: tuple[str, ...] = (
        "No separately attested baseline receipt was supplied; no baseline uplift is claimed.",
        "A passing internal or external campaign is not proof of AGI or superintelligence.",
    )

    @property
    def report_digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_dict(include_digest=False), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def as_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload = {
            "schema_version": "1.0",
            "campaign_id": self.campaign_id,
            "capability_id": self.capability_id,
            "campaign_digest": self.campaign_digest,
            "request_digest": self.request_digest,
            "receipt_digest": self.receipt_digest,
            "source_regression": self.source_regression.as_dict(),
            "target_transfer": self.target_transfer.as_dict(),
            "by_domain": [item.as_dict() for item in self.by_domain],
            "by_mode": {key: value.as_dict() for key, value in sorted(self.by_mode.items())},
            "baseline_comparison_available": self.baseline_comparison_available,
            "limitations": list(self.limitations),
        }
        if include_digest:
            payload["report_digest"] = self.report_digest
        return payload


def _aggregate(case_ids: tuple[str, ...], result_map, metric_map) -> TransferAggregate:
    if not case_ids:
        return TransferAggregate(0, 0, None, None, None, None, None)
    passed = sum(1 for case_id in case_ids if result_map[case_id][0] is True)
    metrics = [metric_map[case_id] for case_id in case_ids]
    n = len(metrics)
    return TransferAggregate(
        cases=n,
        passed=passed,
        pass_rate=round(passed / n, 6),
        mean_uncertainty=round(sum(float(row[3]) for row in metrics) / n, 6),
        mean_calibration_error=round(sum(abs(float(row[1]) - float(row[2])) for row in metrics) / n, 6),
        mean_resource_cost_ms=round(sum(float(row[4]) for row in metrics) / n, 3),
        recovery_rate=round(sum(1 for row in metrics if row[5] is True) / n, 6),
    )


def build_cross_domain_report(
    campaign: CrossDomainCampaign,
    evidence: SealedArenaEvidence,
    *,
    expected_request_digest: str,
) -> CrossDomainTransferReport:
    """Build source/target and mode-separated metrics from validated signed evidence."""
    if not isinstance(campaign, CrossDomainCampaign):
        raise TypeError("a validated CrossDomainCampaign is required")
    if not isinstance(evidence, SealedArenaEvidence):
        raise TypeError("validated SealedArenaEvidence is required")
    if not isinstance(campaign.capability_id, str) or not campaign.capability_id.strip():
        raise ValueError("capability_id is required for capability-specific transfer reporting")
    if evidence.request_digest != expected_request_digest:
        raise ValueError("evidence request digest does not match the evaluated campaign")
    for domain in campaign.domains:
        if domain.novel is True:
            provenance = domain.provenance
            if provenance is None:
                raise ValueError(f"novel target domain {domain.domain_id} lacks held-out provenance")
            if provenance.manifest_digest != campaign.request.manifest_digest:
                raise ValueError(f"target domain {domain.domain_id} provenance manifest mismatch")
            if provenance.development_corpus_digest == campaign.request.corpus_digest:
                raise ValueError(f"target domain {domain.domain_id} reuses the development corpus")
    result_map = {row[0]: (row[1], row[2]) for row in evidence.case_results}
    metric_map = {row[0]: row for row in evidence.case_metrics}
    holdouts = {case.case_id for case in campaign.request.cases if case.holdout is True}
    if not holdouts or not holdouts <= set(result_map):
        raise ValueError("validated evidence is missing one or more sealed holdout outcomes")
    if not holdouts <= set(metric_map):
        raise ValueError("cross-domain report requires signed metrics for every holdout case")

    domain_by_case = {
        case_id: domain
        for domain in campaign.domains
        for case_id in domain.case_ids
    }
    if not holdouts <= set(domain_by_case):
        raise ValueError("cross-domain report has unassigned holdout cases")

    by_domain: list[DomainTransferMetrics] = []
    for domain in campaign.domains:
        ids = tuple(case_id for case_id in domain.case_ids if case_id in holdouts)
        by_domain.append(DomainTransferMetrics(
            domain.domain_id, domain.novel, domain.evaluation_mode,
            _aggregate(ids, result_map, metric_map),
        ))

    source_ids = tuple(sorted(
        case_id for case_id in holdouts if domain_by_case[case_id].novel is False
    ))
    target_ids = tuple(sorted(
        case_id for case_id in holdouts if domain_by_case[case_id].novel is True
    ))
    if not source_ids:
        raise ValueError("source-domain holdout is required to report source regression")
    if not target_ids:
        raise ValueError("novel target-domain holdout is required to report transfer")

    by_mode = {
        mode: _aggregate(
            tuple(sorted(case_id for case_id in holdouts if domain_by_case[case_id].evaluation_mode == mode)),
            result_map,
            metric_map,
        )
        for mode in ("zero_shot", "few_shot", "adapted")
    }
    return CrossDomainTransferReport(
        campaign_id=campaign.campaign_id,
        capability_id=campaign.capability_id,
        campaign_digest=campaign.campaign_digest,
        request_digest=evidence.request_digest,
        receipt_digest=evidence.receipt_digest,
        source_regression=_aggregate(source_ids, result_map, metric_map),
        target_transfer=_aggregate(target_ids, result_map, metric_map),
        by_domain=tuple(by_domain),
        by_mode=by_mode,
    )


__all__ = [
    "TransferAggregate", "DomainTransferMetrics", "CrossDomainTransferReport",
    "build_cross_domain_report",
]
