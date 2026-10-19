from __future__ import annotations

from pathlib import Path

import pytest

from portable.adaptive_experiment_controller import AdaptiveExperimentResult
from portable.engineering_episode import EngineeringEpisode
from portable.experiment_learning_bridge import ExperimentLearningBridge, CurriculumLearningSignal
from portable.persistent_evidence_graph import PersistentEvidenceGraph
from portable.persistent_experiment_learning import (
    ExperimentManifest,
    LearningEvidenceError,
    LearningStoreConflict,
    PersistentExperimentLearningStore,
)
from portable.persistent_memory import PersistentMemory


def manifest(experiment_id="exp-1", **changes):
    values = dict(
        experiment_id=experiment_id,
        capability="planner",
        intervention_digest="intervention-digest",
        task_digest=f"task-digest-{experiment_id}",
        task_split_digest="sealed-split-digest",
        harness_digest="harness-v4",
        model_id="model-a",
        provider_id="provider-a",
        executor_id="executor-a",
        evaluator_id="evaluator-a",
        policy_digest="policy-digest",
        environment_digest="environment-digest",
        budget_digest="budget-digest",
        isolation_attestation_digest=f"isolation-{experiment_id}",
        seed=17,
    )
    values.update(changes)
    return ExperimentManifest(**values)


def result(experiment_id="exp-1", **changes):
    values = dict(
        experiment_id=experiment_id,
        intervention_digest="intervention-digest",
        replication_count=3,
        observation_count=9,
        aggregated_treatment_lift=0.08,
        aggregated_holdout_lift=0.04,
        replication_consistency=1.0,
        regression_detected=False,
        stop=True,
        rollback_recommended=False,
        evidence_sufficient=True,
        reasons=("minimum evidence criteria met",),
    )
    values.update(changes)
    return AdaptiveExperimentResult(**values)


def store(tmp_path, *, evidence=True, promotion=True, retraction=True):
    memory = PersistentMemory(Path(tmp_path) / "memory.sqlite3", require_approval=False)
    return PersistentExperimentLearningStore(
        memory,
        "project-a",
        verify_evidence=lambda refs, m, r: evidence,
        verify_promotion=lambda receipt_id, reviewer_id, ref: promotion,
        verify_retraction=lambda receipt_id, reason, ref: retraction,
    )


def recorded(s, m=None, r=None, *, evidence_refs=("arena-run:receipt-1",), episode=None, domain_lifts=()):
    m = m or manifest()
    r = r or result(experiment_id=m.experiment_id)
    signal = ExperimentLearningBridge().derive(
        capability=m.capability, result=r, domain_lifts=domain_lifts,
    )
    return s.record(
        manifest=m, result=r, signal=signal, evidence_refs=evidence_refs,
        episode=episode, domain_lifts=domain_lifts,
    )


def test_candidate_is_durable_but_not_retrieved_until_independent_promotion(tmp_path):
    s = store(tmp_path)
    m = manifest()
    receipt = recorded(s, m)
    assert receipt.disposition == "candidate"
    assert s.retrieve_compatible(m) == ()
    promotion = s.promote_candidate(receipt.receipt_id, reviewer_id="independent-reviewer", evidence_ref="review:receipt-1")
    assert promotion
    found = s.retrieve_compatible(m)
    assert len(found) == 1
    assert found[0].receipt_id == receipt.receipt_id
    assert s.graph.lineage(s.graph.node_id("learning_receipt", receipt.receipt_id), direction="out")


def test_record_is_idempotent_and_changed_replay_conflicts(tmp_path):
    s = store(tmp_path)
    m = manifest()
    first = recorded(s, m)
    second = recorded(s, m)
    assert first.receipt_id == second.receipt_id
    changed = result(experiment_id=m.experiment_id, aggregated_treatment_lift=0.12)
    with pytest.raises(LearningStoreConflict, match="different outcome"):
        recorded(s, m, changed)


def test_unverified_or_insufficient_result_is_unscored_and_never_promoted(tmp_path):
    s = store(tmp_path, evidence=False)
    m = manifest()
    receipt = recorded(s, m)
    assert receipt.disposition == "unscored"
    with pytest.raises(LearningEvidenceError, match="only evidence-verified candidates"):
        s.promote_candidate(receipt.receipt_id, reviewer_id="reviewer", evidence_ref="review:1")

    s2 = store(Path(tmp_path) / "second")
    small = result(experiment_id="small", replication_count=1, evidence_sufficient=False, reasons=("insufficient",))
    m2 = manifest(experiment_id="small")
    receipt2 = recorded(s2, m2, small)
    assert receipt2.disposition == "unscored"


def test_incompatible_runtime_stack_cannot_reuse_a_lesson(tmp_path):
    s = store(tmp_path)
    original = manifest()
    receipt = recorded(s, original)
    s.promote_candidate(receipt.receipt_id, reviewer_id="reviewer", evidence_ref="review:1")
    incompatible = manifest("exp-2", model_id="model-b")
    assert s.retrieve_compatible(incompatible) == ()


def test_conflicting_verified_negative_result_quarantines_positive_candidate(tmp_path):
    s = store(tmp_path)
    positive_manifest = manifest("positive")
    positive = recorded(s, positive_manifest)
    s.promote_candidate(positive.receipt_id, reviewer_id="reviewer", evidence_ref="review:positive")
    negative_manifest = manifest("negative")
    negative_result = result(
        experiment_id="negative", regression_detected=True, rollback_recommended=True,
        aggregated_holdout_lift=-0.08, reasons=("holdout regression",),
    )
    negative = recorded(s, negative_manifest, negative_result, evidence_refs=("arena-run:receipt-2",))
    assert negative.disposition == "negative"
    assert s.retrieve_compatible(positive_manifest) == ()


def test_retraction_removes_previously_promoted_lesson_and_is_idempotent(tmp_path):
    s = store(tmp_path)
    m = manifest()
    receipt = recorded(s, m)
    s.promote_candidate(receipt.receipt_id, reviewer_id="reviewer", evidence_ref="review:1")
    assert len(s.retrieve_compatible(m)) == 1
    digest = s.retract(receipt.receipt_id, reason="independent replay found contamination", evidence_ref="retraction:1")
    assert digest
    assert s.retract(receipt.receipt_id, reason="independent replay found contamination", evidence_ref="retraction:1") == digest
    assert s.retrieve_compatible(m) == ()


def test_retraction_requires_independent_evidence(tmp_path):
    s = store(tmp_path, retraction=False)
    receipt = recorded(s)
    with pytest.raises(LearningEvidenceError, match="retraction evidence"):
        s.retract(receipt.receipt_id, reason="remove", evidence_ref="unverified")


def test_signal_cannot_be_forged_independently_of_result(tmp_path):
    s = store(tmp_path)
    m = manifest()
    r = result()
    genuine = ExperimentLearningBridge().derive(capability=m.capability, result=r)
    forged = CurriculumLearningSignal(
        experiment_id=genuine.experiment_id,
        capability=genuine.capability,
        action="continue",
        failed_domains=genuine.failed_domains,
        next_conditions=genuine.next_conditions,
        evidence_digest=genuine.evidence_digest,
    )
    with pytest.raises(LearningStoreConflict, match="signal does not match"):
        s.record(manifest=m, result=r, signal=forged, evidence_refs=("arena-run:receipt-1",))


def test_episode_is_linked_into_the_same_evidence_graph(tmp_path):
    s = store(tmp_path)
    episode = (
        EngineeringEpisode.start(
            episode_id="episode-1", task_id="task-1", project="project-a",
            repository_snapshot="snapshot-digest", intent_digest="intent-digest",
            evidence_ids=("repo-snapshot:1",),
        )
        .with_plan("plan-digest", capability="planner")
        .start_execution()
        .begin_verification()
        .record_verification("verify:1")
        .begin_review()
        .record_review("review:1")
        .complete(outcome="passed", regression_ids=("regression:1",))
    )
    receipt = recorded(s, episode=episode)
    episode_node = s.graph.node_id("engineering_episode", episode.digest)
    lineage = s.graph.lineage(episode_node, direction="in")
    assert any(edge.source_id == s.graph.node_id("learning_receipt", receipt.receipt_id) for edge in lineage)


def test_retrieval_age_and_limit_are_bounded(tmp_path):
    s = store(tmp_path)
    m = manifest()
    receipt = recorded(s, m)
    s.promote_candidate(receipt.receipt_id, reviewer_id="reviewer", evidence_ref="review:1")
    assert s.retrieve_compatible(m, max_age_seconds=0.001) == ()
    with pytest.raises(ValueError, match="limit"):
        s.retrieve_compatible(m, limit=0)


def test_manifest_requires_explicit_isolation_attestation_and_integer_seed():
    values = manifest().as_dict()
    values["isolation_attestation_digest"] = ""
    with pytest.raises(ValueError, match="isolation_attestation_digest"):
        ExperimentManifest(**values)
    values = manifest().as_dict()
    values["seed"] = True
    with pytest.raises(ValueError, match="seed"):
        ExperimentManifest(**values)
