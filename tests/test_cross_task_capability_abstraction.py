"""Tests for cross-task abstraction and held-out transfer gates."""
from pathlib import Path
from tempfile import TemporaryDirectory
from portable.cross_task_capability_abstraction import CrossTaskCapabilityAbstraction


def _context():
    return {"complexity": .8, "dependency_parallelism": .7, "resource_pressure": .2,
            "latency_pressure": .3, "evidence_value": .9, "failure_risk": .2}


def test_context_signature_is_provider_free_and_stable():
    assert CrossTaskCapabilityAbstraction.context_signature(_context()).count("|") == 5


def test_discover_requires_repeated_verified_training_evidence():
    with TemporaryDirectory() as td:
        engine = CrossTaskCapabilityAbstraction(Path(td), minimum_train_samples=3)
        for i in range(3):
            engine.record_episode(role="team", task=f"train-{i}", strategy="evidence-first",
                                  mode="parallel", context=_context(), outcome="passed",
                                  evidence_quality=.9, evidence_ids=[f"train:{i}"])
        patterns = engine.discover()
        assert len(patterns) == 1
        assert patterns[0].train_success_rate == 1.0


def test_holdout_promotion_requires_candidate_and_baseline_evidence():
    with TemporaryDirectory() as td:
        engine = CrossTaskCapabilityAbstraction(Path(td), minimum_train_samples=3, minimum_uplift=.03)
        for i in range(3):
            engine.record_episode(role="team", task=f"train-{i}", strategy="evidence-first",
                                  mode="parallel", context=_context(), outcome="passed",
                                  evidence_quality=.9, evidence_ids=[f"train:{i}"])
        pattern = engine.discover()[0]
        holdouts = [
            {"task":"holdout-a","outcome":"passed","evidence_ids":["h:a"],
             "detail":'{"decision":{"strategy":"evidence-first","mode":"parallel","context":'+__import__("json").dumps(_context())+'},"evidence_quality":0.9}'},
            {"task":"holdout-b","outcome":"failed","evidence_ids":["h:b"],
             "detail":'{"decision":{"strategy":"default","mode":"balanced","context":'+__import__("json").dumps(_context())+'},"evidence_quality":0.2}'},
        ]
        result = engine.validate(pattern, holdouts)
        assert result.promoted
        assert result.uplift > .03


def test_hypothesis_synthesis_is_bounded():
    with TemporaryDirectory() as td:
        engine = CrossTaskCapabilityAbstraction(Path(td))
        a = engine.discover()
        assert engine.synthesize_hypotheses(a) == ()
