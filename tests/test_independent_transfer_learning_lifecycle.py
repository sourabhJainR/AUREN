from portable.independent_transfer_learning_lifecycle import (
    CausalAttributionEstimator,
    CrossDomainTransferMeasurer,
    IndependentBenchmarkGenerator,
    IndependentTransferLearningLifecycle,
    PromotionRollbackLedger,
)
from portable.arena_run_receipt import ArenaRunReceipt, oracle_registry_digest


def benchmark():
    return IndependentBenchmarkGenerator().generate(
        benchmark_id="transfer-v1",
        version="1",
        generator_id="external-generator",
        runtime_id="auren-runtime",
        oracle_ids=("external-oracle",),
        corpus_digest="corpus-1",
        domains={
            "code": (("code-1", "task-code-1", False), ("code-2", "task-code-2", False)),
            "math": (("math-1", "task-math-1", True), ("math-2", "task-math-2", True)),
            "science": (("science-1", "task-science-1", True), ("science-2", "task-science-2", True)),
        },
        holdout_domains=("math", "science"),
    )


def test_independent_generation_seals_disjoint_domain_split():
    result = benchmark()
    assert result.manifest.train_domains == ("code",)
    assert result.manifest.holdout_domains == ("math", "science")
    assert all(case.holdout for case in result.request.cases if case.case_id.startswith(("math-", "science-")))


def test_transfer_requires_real_target_domains_and_measures_lift():
    measurement = CrossDomainTransferMeasurer().measure(
        source_domain="code",
        outcomes=(
            ("code", "control", True, .70, False, False),
            ("math", "control", True, .60, False, True),
            ("math", "treatment", True, .70, False, True),
            ("science", "control", True, .55, False, True),
            ("science", "treatment", True, .68, False, True),
        ),
    )
    assert measurement.target_domains == ("math", "science")
    assert measurement.transfer_lift > .03
    assert measurement.valid


def test_lifecycle_promotes_only_after_causal_and_transfer_gates():
    result = IndependentTransferLearningLifecycle().evaluate(
        benchmark=benchmark(),
        capability_id="transfer-capability",
        intervention_id="intervention-1",
        baseline_score=.60,
        control_score=.61,
        treatment_score=.72,
        holdout_score=.66,
        attribution_confidence=.90,
        randomized_assignment=True,
        control_scores=(.61, .60, .62, .61),
        treatment_scores=(.72, .71, .73, .72),
        run_receipt=ArenaRunReceipt(
            run_id="run-1",
            arena_version="1",
            corpus_digest=benchmark().request.corpus_digest,
            manifest_digest=benchmark().manifest.digest,
            oracle_digest=oracle_registry_digest(benchmark().manifest.oracle_ids),
            runtime_snapshot="runtime-1",
            evaluator_version="eval-1",
            case_ids=tuple(case.case_id for case in benchmark().request.cases),
            holdout_case_ids=("math-1", "math-2", "science-1", "science-2"),
            passed_case_ids=tuple(case.case_id for case in benchmark().request.cases),
            verified_case_ids=tuple(case.case_id for case in benchmark().request.cases),
            duration_ms=10,
        ),
        transfer=CrossDomainTransferMeasurer().measure(
            source_domain="code",
            outcomes=(
                ("code", "control", True, .70, False, False),
                ("math", "control", True, .60, False, True),
                ("math", "treatment", True, .70, False, True),
                ("science", "control", True, .55, False, True),
                ("science", "treatment", True, .68, False, True),
            ),
        ),
        fresh_holdout=True,
        independent_oracle=True,
        evidence_ids=("execution-1", "verification-1", "attribution-1"),
        prior_campaign_digest="prior-campaign",
        version="v2",
    )
    assert result.causal.eligible
    assert result.promotion is not None
    assert result.promotion.version == "v2"


def test_rollback_restores_previous_version():
    ledger = PromotionRollbackLedger()
    first = ledger.promote("cap", "v1", "e1")
    second = ledger.promote("cap", "v2", "e2")
    assert second.prior_version == first.version
    restored = ledger.rollback("cap", reason="holdout regression")
    assert restored.version == "v1"
    assert ledger.active("cap").version == "v1"


def test_causal_attribution_requires_randomization_and_independent_holdout():
    attribution = CausalAttributionEstimator.estimate(
        (.60, .62, .61, .59),
        (.72, .70, .73, .71),
        randomized=True,
        independent_holdout=True,
    )
    assert attribution.attributable
    assert attribution.treatment_effect > 0
    assert attribution.confidence > 0
