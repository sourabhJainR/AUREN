from portable.adaptive_experiment_controller import AdaptiveExperimentResult
from portable.experiment_learning_bridge import ExperimentLearningBridge


def result(**kw):
    base=dict(experiment_id="e",intervention_digest="i",replication_count=3,observation_count=9,
              aggregated_treatment_lift=.08,aggregated_holdout_lift=.04,
              replication_consistency=1.0,regression_detected=False,stop=True,
              rollback_recommended=False,evidence_sufficient=True,reasons=("ok",))
    base.update(kw)
    return AdaptiveExperimentResult(**base)


def test_success_routes_to_causal_review():
    s=ExperimentLearningBridge().derive(capability="planner",result=result(),
        domain_lifts=(("planning",.08),("tools",.03),("vision",-.01)))
    assert s.action=="causal-review"
    assert s.failed_domains==("vision",)
    assert s.evidence_digest==result().result_digest


def test_regression_routes_to_rollback():
    s=ExperimentLearningBridge().derive(capability="planner",result=result(
        regression_detected=True,rollback_recommended=True,evidence_sufficient=False))
    assert s.action=="rollback"


def test_small_sample_routes_to_more_evidence():
    s=ExperimentLearningBridge().derive(capability="planner",result=result(
        replication_count=1,evidence_sufficient=False,stop=True))
    assert s.action=="insufficient-evidence"
