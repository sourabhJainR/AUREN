import unittest
from dataclasses import replace

from portable.sealed_arena_boundary import (
    ExternalOutcomeReceipt,
    SealedArenaBoundary,
    SealedCampaignRequest,
    SealedCaseEnvelope,
)
from portable.sealed_cross_domain_arena import CrossDomainCampaign, SealedDomain, SealedCrossDomainRunner
from portable.external_evaluator_gateway import ExternalEvaluatorCommand
from portable.cross_domain_transfer_metrics import build_cross_domain_report


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
    campaign = CrossDomainCampaign(
        "transfer-campaign",
        request,
        (
            SealedDomain("source", ("source-case",), False, "zero_shot"),
            SealedDomain("target-few-shot", ("few-case",), True, "few_shot"),
            SealedDomain("target-adapted", ("adapted-case",), True, "adapted"),
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

        runner = SealedCrossDomainRunner(StubGateway())
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


if __name__ == "__main__":
    unittest.main()
