import unittest
from dataclasses import replace

from portable.sealed_arena_boundary import (
    ExternalOutcomeReceipt,
    SealedArenaBoundary,
    SealedCampaignRequest,
    SealedCaseEnvelope,
)
from portable.sealed_cross_domain_arena import CrossDomainCampaign, SealedDomain, SealedCrossDomainRunner, TargetDomainProvenance
from portable.external_evaluator_gateway import ExternalEvaluatorCommand
from portable.cross_domain_transfer_metrics import build_cross_domain_report
from portable.sealed_corpus_provenance import SealedCorpusProvenance


def target_provenance(domain_id, manifest_digest):
    return TargetDomainProvenance(
        domain_id=domain_id,
        origin="https://benchmarks.example.org/transfer/" + domain_id,
        manifest_digest=manifest_digest,
        split="sealed_holdout",
        development_corpus_digest="development-corpus-v0",
        development_overlap_status="no_overlap",
        overlap_attestation_digest="overlap-verified:" + domain_id,
        independent_operator_id="independent-operator",
        attestation_digest="verified:" + domain_id,
    )


def setup_campaign(capability_id="planner-v1"):
    request = SealedCampaignRequest(
        "campaign-base", "corpus-v1",
        (
            SealedCaseEnvelope("source-case", "task-s", "input-s", "env-s", True, ("source",)),
            SealedCaseEnvelope("few-case", "task-f", "input-f", "env-f", True, ("novel",)),
            SealedCaseEnvelope("adapted-case", "task-a", "input-a", "env-a", True, ("novel",)),
        ),
        "metrics-run-1",
    )
    provenance = SealedCorpusProvenance(
        corpus_digest=request.corpus_digest,
        manifest_digest=request.manifest_digest,
        origin="https://benchmarks.example.org/transfer/holdout-v1",
        split="sealed_holdout",
        contamination_status="clean",
        independent_operator_id="independent-operator",
        attestation_digest="transfer-attestation-v1",
    )
    request = replace(request, provenance=provenance)
    campaign = CrossDomainCampaign(
        "transfer-campaign",
        request,
        (
            SealedDomain("source", ("source-case",), False, "zero_shot"),
            SealedDomain("target-few-shot", ("few-case",), True, "few_shot",
                         target_provenance("target-few-shot", request.manifest_digest)),
            SealedDomain("target-adapted", ("adapted-case",), True, "adapted",
                         target_provenance("target-adapted", request.manifest_digest)),
        ),
        "independent-oracle",
        "independent-evaluator",
        capability_id,
    )
    bound_request = replace(request, campaign_digest=campaign.campaign_digest)
    receipt = ExternalOutcomeReceipt(
        bound_request.campaign_digest,
        bound_request.corpus_digest,
        "independent-evaluator",
        "independent-oracle",
        "signed",
        (
            ("source-case", False, "evidence-source"),
            ("few-case", True, "evidence-few"),
            ("adapted-case", True, "evidence-adapted"),
        ),
        run_nonce=bound_request.run_nonce,
        request_digest=bound_request.request_digest,
        contamination_free=True,
        independent_oracle=True,
        metrics=(
            ("source-case", 0.80, 0.70, 0.20, 100.0, True),
            ("few-case", 0.60, 0.90, 0.30, 250.0, True),
            ("adapted-case", 0.90, 0.80, 0.10, 300.0, False),
        ),
    )
    evidence = SealedArenaBoundary(lambda value: value.signature == "signed").accept(
        bound_request, receipt
    )
    return campaign, bound_request, receipt, evidence


class CrossDomainTransferMetricsTests(unittest.TestCase):
    def test_reports_source_regression_target_transfer_and_modes_separately(self):
        campaign, bound_request, _receipt, evidence = setup_campaign()
        report = build_cross_domain_report(
            campaign, evidence, expected_request_digest=bound_request.request_digest
        )
        self.assertEqual(report.capability_id, "planner-v1")
        self.assertEqual(report.source_regression.pass_rate, 0.0)
        self.assertEqual(report.target_transfer.pass_rate, 1.0)
        self.assertEqual(report.by_mode["zero_shot"].pass_rate, 0.0)
        self.assertEqual(report.by_mode["few_shot"].pass_rate, 1.0)
        self.assertEqual(report.by_mode["adapted"].pass_rate, 1.0)
        self.assertEqual(report.by_mode["few_shot"].mean_resource_cost_ms, 250.0)
        self.assertEqual(report.by_mode["adapted"].recovery_rate, 0.0)
        self.assertFalse(report.baseline_comparison_available)
        self.assertEqual(report.report_digest, report.as_dict()["report_digest"])

    def test_runner_returns_report_from_gateway_validated_evidence(self):
        campaign, _bound_request, _receipt, evidence = setup_campaign()

        class StubGateway:
            def evaluate(self, _request, _command):
                return evidence

        runner = SealedCrossDomainRunner(StubGateway(), target_provenance_verifier=lambda value: value.attestation_digest.startswith("verified:"))
        report = runner.run_with_report(
            campaign, ExternalEvaluatorCommand(("python", "-c", "pass"))
        )
        self.assertEqual(report.campaign_id, campaign.campaign_id)
        self.assertEqual(report.target_transfer.pass_rate, 1.0)

    def test_missing_signed_holdout_metrics_fail_closed(self):
        campaign, bound_request, receipt, _evidence = setup_campaign()
        incomplete = replace(receipt, metrics=())
        evidence = SealedArenaBoundary(lambda value: value.signature == "signed").accept(
            bound_request, incomplete
        )
        with self.assertRaisesRegex(ValueError, "signed metrics"):
            build_cross_domain_report(
                campaign, evidence, expected_request_digest=bound_request.request_digest
            )

    def test_wrong_request_lineage_is_rejected(self):
        campaign, _bound_request, _receipt, evidence = setup_campaign()
        with self.assertRaisesRegex(ValueError, "request digest"):
            build_cross_domain_report(campaign, evidence, expected_request_digest="wrong")

    def test_capability_identity_is_required_for_capability_specific_report(self):
        campaign, bound_request, _receipt, evidence = setup_campaign(capability_id="")
        with self.assertRaisesRegex(ValueError, "capability_id"):
            build_cross_domain_report(
                campaign, evidence, expected_request_digest=bound_request.request_digest
            )

    def test_metric_values_are_strictly_validated(self):
        campaign, bound_request, receipt, _evidence = setup_campaign()
        with self.assertRaisesRegex(ValueError, "predicted_score"):
            invalid = replace(receipt, metrics=(("source-case", float("nan"), 0.7, 0.2, 1.0, True),))
            SealedArenaBoundary(lambda value: value.signature == "signed").accept(
                bound_request, invalid
            )


    def test_runner_requires_verified_target_provenance(self):
        campaign, _bound_request, _receipt, evidence = setup_campaign()
        class StubGateway:
            def evaluate(self, _request, _command):
                return evidence
        with self.assertRaisesRegex(ValueError, "provenance verifier"):
            SealedCrossDomainRunner(StubGateway()).run_with_report(
                campaign, ExternalEvaluatorCommand(("python", "-c", "pass"))
            )
        runner = SealedCrossDomainRunner(
            StubGateway(), target_provenance_verifier=lambda _value: False
        )
        with self.assertRaisesRegex(ValueError, "not verified"):
            runner.run_with_report(campaign, ExternalEvaluatorCommand(("python", "-c", "pass")))

    def test_report_rejects_missing_or_development_overlapping_target_provenance(self):
        campaign, bound_request, _receipt, evidence = setup_campaign()
        missing_domain = replace(campaign.domains[1], provenance=None)
        missing = replace(campaign, domains=(campaign.domains[0], missing_domain, campaign.domains[2]))
        with self.assertRaisesRegex(ValueError, "lacks held-out provenance"):
            build_cross_domain_report(missing, evidence, expected_request_digest=bound_request.request_digest)
        bad_provenance = replace(
            campaign.domains[1].provenance,
            development_corpus_digest=campaign.request.corpus_digest,
        )
        bad_domain = replace(campaign.domains[1], provenance=bad_provenance)
        overlapping = replace(campaign, domains=(campaign.domains[0], bad_domain, campaign.domains[2]))
        with self.assertRaisesRegex(ValueError, "reuses the development corpus"):
            build_cross_domain_report(overlapping, evidence, expected_request_digest=bound_request.request_digest)


if __name__ == "__main__":
    unittest.main()
